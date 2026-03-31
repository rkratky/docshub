import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions, request_ctx
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    InitializedNotification,
    PromptReference,
    ResourceTemplateReference,
    ToolAnnotations,
)
from pydantic import BaseModel, Field

mcp = FastMCP(
    "docshub",
    instructions=(
        "DocsHub provides access to developer documentation via llms.txt files. "
        "Available tools: "
        "list_available_docs — discover configured projects (always call first); "
        "get_project_docs — fetch a project's documentation (tries llms-full.txt "
        "for complete docs, falls back to llms.txt index of URLs); "
        "read_doc_page — fetch a specific page URL from an llms.txt index. "
        "Available resources: "
        "docshub://projects — JSON registry of all configured projects; "
        "docshub://project/{name}/docs — raw documentation content for a project. "
        "Available prompts: "
        "query_docs — answer a question using a project's documentation; "
        "summarize_project — produce a structured overview of a project's docs."
    ),
)

_BUNDLED_CONFIG = Path(__file__).parent / "docs_config.yaml"

_CONFIG_SEARCH_PATHS: list[str] = [
    os.environ.get("DOCSHUB_CONFIG", ""),
    "docs_config.yaml",
    os.path.expanduser("~/.config/docshub/docs_config.yaml"),
    str(_BUNDLED_CONFIG),
]

_CONFIG_RELOAD_INTERVAL: int = int(os.environ.get("DOCSHUB_CONFIG_RELOAD_INTERVAL", "900"))
_CACHE_TTL: int = int(os.environ.get("DOCSHUB_CACHE_TTL", "1800"))

_READ_ONLY_WEB_TOOL = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=True,
)


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    content: str
    source_url: str
    expires_at: float


_docs_cache: dict[str, _CacheEntry] = {}


def _cache_get(name: str) -> tuple[str, str] | None:
    """Return cached (content, source_url) if not expired, else None."""
    entry = _docs_cache.get(name)
    if entry and time.monotonic() < entry.expires_at:
        return entry.content, entry.source_url
    return None


def _cache_set(name: str, content: str, source_url: str) -> None:
    """Store (content, source_url) in the cache with a TTL expiry.

    No-op when ``_CACHE_TTL`` is ``0`` (caching disabled).
    """
    if _CACHE_TTL == 0:
        return
    _docs_cache[name] = _CacheEntry(content, source_url, time.monotonic() + _CACHE_TTL)


def _cache_clear() -> None:
    """Invalidate all cached documentation entries."""
    _docs_cache.clear()


# ---------------------------------------------------------------------------
# Config source tracking
# ---------------------------------------------------------------------------

_config_source: str = ""
_config_hash: str = ""


def _hash_text(text: str) -> str:
    """Return a SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _parse_projects(text: str, source: str) -> list[dict[str, Any]]:
    """Parse YAML config text and return the projects list.

    Args:
        text: Raw YAML string to parse.
        source: Human-readable label for the config source (used in warnings).

    Returns:
        List of project dicts from the 'projects' key, or an empty list if the
        YAML is empty, malformed, or missing the 'projects' key.
    """
    try:
        parsed = yaml.safe_load(text) or {}
        return parsed.get("projects", []) if isinstance(parsed, dict) else []
    except yaml.YAMLError as e:
        print(f"Warning: Failed to parse config from {source}: {e}", file=sys.stderr)
        return []


def load_config() -> list[dict[str, Any]]:
    """Load project configuration from the first available config source.

    Sources are tried in order:

    1. ``DOCSHUB_CONFIG`` env var (local path or ``https://`` URL).
    2. ``docs_config.yaml`` in the working directory.
    3. ``~/.config/docshub/docs_config.yaml``.
    4. Bundled default shipped with the package.

    Returns:
        List of project dicts parsed from the YAML ``projects`` key.
        Returns an empty list when no source is found or all sources fail.
    """
    global _config_source, _config_hash
    for source in _CONFIG_SEARCH_PATHS:
        if not source:
            continue
        if source.startswith("https://"):
            try:
                response = httpx.get(source, follow_redirects=True, timeout=10)
                response.raise_for_status()
                _config_source = source
                _config_hash = _hash_text(response.text)
                return _parse_projects(response.text, source)
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                print(
                    f"Warning: Failed to fetch remote config from {source}: {e}",
                    file=sys.stderr,
                )
            continue
        if source.startswith("http://"):
            print(
                f"Warning: Skipping insecure http:// config source '{source}'. Use https://.",
                file=sys.stderr,
            )
            continue
        try:
            with open(source) as file:
                text = file.read()
            _config_source = source
            _config_hash = _hash_text(text)
            return _parse_projects(text, source)
        except FileNotFoundError:
            continue
    return []


PROJECTS = {
    proj["name"]: proj
    for proj in load_config()
    if isinstance(proj, dict) and "name" in proj and "llms_txt_url" in proj
}

if not PROJECTS:
    print(
        "Warning: No projects loaded. Provide a docs_config.yaml or set DOCSHUB_CONFIG.",
        file=sys.stderr,
    )

# Allowlist of hostnames derived from configured project URLs.
_ALLOWED_HOSTS: frozenset[str] = frozenset(
    parsed.hostname
    for proj in PROJECTS.values()
    if (parsed := urlparse(proj["llms_txt_url"])).hostname
)


def _derive_full_txt_url(llms_txt_url: str) -> str:
    """Derive the llms-full.txt URL by replacing the llms.txt suffix.

    Args:
        llms_txt_url: The base ``llms.txt`` URL for a project.

    Returns:
        The ``llms-full.txt`` URL if ``llms_txt_url`` ends with ``llms.txt``,
        otherwise an empty string.
    """
    if llms_txt_url.endswith("llms.txt"):
        return llms_txt_url[: -len("llms.txt")] + "llms-full.txt"
    return ""


async def _fetch_uncached(name: str) -> tuple[str, str]:
    """Fetch docs from the network (no caching). See _fetch_project_content."""
    llms_txt_url = PROJECTS[name]["llms_txt_url"]
    full_url = _derive_full_txt_url(llms_txt_url)
    async with httpx.AsyncClient() as client:
        if full_url:
            try:
                response = await client.get(full_url, follow_redirects=True, timeout=30)
                response.raise_for_status()
                return response.text, full_url
            except (httpx.HTTPStatusError, httpx.RequestError):
                pass
        response = await client.get(llms_txt_url, follow_redirects=True, timeout=10)
        response.raise_for_status()
        return response.text, llms_txt_url


async def _fetch_project_content(name: str) -> tuple[str, str]:
    """Fetch documentation for a named project, using the in-memory cache.

    Tries ``llms-full.txt`` first for complete documentation; falls back to
    ``llms.txt`` when the full variant is unavailable. Results are cached for
    ``_CACHE_TTL`` seconds to avoid redundant HTTP requests.

    Args:
        name: Project name as it appears in ``PROJECTS``.

    Returns:
        A ``(content, source_url)`` tuple where ``source_url`` identifies
        which file was loaded (full vs index).

    Raises:
        httpx.HTTPStatusError: If the HTTP response has a 4xx/5xx status.
        httpx.RequestError: If a network or connection error occurs.
    """
    cached = _cache_get(name)
    if cached:
        return cached
    content, source_url = await _fetch_uncached(name)
    _cache_set(name, content, source_url)
    return content, source_url


async def _reload_if_changed() -> bool:
    """Re-fetch the config source and reload PROJECTS if content has changed.

    Args: none (uses module-level ``_config_source`` and ``_config_hash``).

    Returns:
        ``True`` if the config was reloaded, ``False`` if unchanged or on error.
    """
    global PROJECTS, _ALLOWED_HOSTS, _config_hash
    if not _config_source or _config_source == str(_BUNDLED_CONFIG):
        return False
    try:
        if _config_source.startswith("https://"):
            async with httpx.AsyncClient() as client:
                response = await client.get(_config_source, follow_redirects=True, timeout=10)
                response.raise_for_status()
                text = response.text
        else:
            with open(_config_source) as f:
                text = f.read()
    except (httpx.HTTPStatusError, httpx.RequestError, OSError):
        return False
    new_hash = _hash_text(text)
    if new_hash == _config_hash:
        return False
    new_list = _parse_projects(text, _config_source)
    PROJECTS = {
        proj["name"]: proj
        for proj in new_list
        if isinstance(proj, dict) and "name" in proj and "llms_txt_url" in proj
    }
    _ALLOWED_HOSTS = frozenset(
        parsed.hostname
        for proj in PROJECTS.values()
        if (parsed := urlparse(proj["llms_txt_url"])).hostname
    )
    _config_hash = new_hash
    _cache_clear()
    return True


# ---------------------------------------------------------------------------
# Session capture and list-changed notifications
# ---------------------------------------------------------------------------

_active_session: Any = None


async def _on_client_initialized(notification: Any) -> None:
    """Capture the active ServerSession when the client completes the handshake.

    FastMCP's low-level server sets ``request_ctx`` during notification
    handling, so the session is accessible here. The captured reference is
    used by the background config watcher to send ``list_changed``
    notifications without requiring an active request context.
    """
    global _active_session
    ctx = request_ctx.get(None)
    if ctx is not None:
        _active_session = ctx.session


mcp._mcp_server.notification_handlers[InitializedNotification] = _on_client_initialized

# Declare listChanged=True so clients know to expect change notifications.
# The background config watcher (started in main()) is responsible for
# sending them via _send_list_changed_notifications().
_notification_options = NotificationOptions(
    tools_changed=True,
    resources_changed=True,
    prompts_changed=True,
)
_orig_create_init = mcp._mcp_server.create_initialization_options


def _patched_create_init(notification_options: Any = None, **kwargs: Any) -> Any:
    return _orig_create_init(
        notification_options=notification_options or _notification_options, **kwargs
    )


mcp._mcp_server.create_initialization_options = _patched_create_init


async def _send_list_changed_notifications() -> None:
    """Send tools/resources/prompts list-changed notifications to the client."""
    if _active_session is None:
        return
    with suppress(Exception):
        await _active_session.send_tool_list_changed()
    with suppress(Exception):
        await _active_session.send_resource_list_changed()
    with suppress(Exception):
        await _active_session.send_prompt_list_changed()


async def _config_watcher() -> None:
    """Background task that polls the config source for changes.

    Wakes every ``_CONFIG_RELOAD_INTERVAL`` seconds. When the config content
    hash changes, reloads ``PROJECTS`` / ``_ALLOWED_HOSTS``, clears the docs
    cache, and notifies the connected client via list-changed notifications.
    The bundled default config is never polled (it changes only on upgrades).
    Does nothing when ``_CONFIG_RELOAD_INTERVAL`` is ``0`` (polling disabled).
    """
    if _CONFIG_RELOAD_INTERVAL == 0:
        return
    while True:
        await asyncio.sleep(_CONFIG_RELOAD_INTERVAL)
        try:
            if await _reload_if_changed():
                await _send_list_changed_notifications()
        except Exception as e:
            print(f"Warning: Config watcher error: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Elicitation schema
# ---------------------------------------------------------------------------


class _ProjectSelection(BaseModel):
    """Elicitation schema for interactive project selection."""

    project_name: str = Field(description="Name of the documentation project to fetch.")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(title="List Available Docs", annotations=_READ_ONLY_WEB_TOOL)
def list_available_docs() -> list[dict[str, str]]:
    """List all configured documentation projects.

    Always call this first to discover available project names before using
    ``get_project_docs``. Pass the exact value of the ``name`` field to
    ``get_project_docs``.

    Returns:
        List of dicts with ``name`` and ``description`` keys.
        Returns an empty list if no projects are configured.
    """
    return [
        {
            "name": name,
            "description": info.get("description", ""),
        }
        for name, info in PROJECTS.items()
    ]


@mcp.tool(title="Fetch Project Documentation", annotations=_READ_ONLY_WEB_TOOL)
async def get_project_docs(
    project_name: Annotated[
        str, Field(description="Exact project name as returned by list_available_docs.")
    ],
    ctx: Context,
) -> str:
    """Fetch documentation for a project.

    Automatically tries ``llms-full.txt`` first, which returns the complete
    documentation in a single response — no further calls needed.
    If ``llms-full.txt`` is unavailable, falls back to ``llms.txt`` and returns
    an index of URLs; use ``read_doc_page`` with those URLs to retrieve individual
    pages.

    Call ``list_available_docs`` first to get valid project names.

    Args:
        project_name: Exact project name as returned by ``list_available_docs``.
        ctx: MCP context used for protocol-level logging.

    Returns:
        Documentation content string with a header indicating the source,
        or an error message string if the project is not found or fetch fails.
    """
    if project_name not in PROJECTS:
        try:
            result = await ctx.elicit(
                f"Project '{project_name}' not found. Choose from: {', '.join(PROJECTS)}.",
                schema=_ProjectSelection,
            )
            if result.action == "accept" and result.data:
                project_name = result.data.project_name
        except Exception:
            pass
        if project_name not in PROJECTS:
            return (
                f"Error: Project '{project_name}' not found."
                " Use list_available_docs to see available options."
            )

    await ctx.report_progress(0, None, f"Fetching documentation for '{project_name}'…")

    llms_txt_url = PROJECTS[project_name]["llms_txt_url"]
    full_url = _derive_full_txt_url(llms_txt_url)

    await ctx.debug(f"Fetching documentation for '{project_name}'")
    try:
        content, source_url = await _fetch_project_content(project_name)
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        await ctx.error(f"Failed to fetch documentation for '{project_name}': {e}")
        raise

    await ctx.report_progress(1, 1, "Documentation loaded")

    if source_url == full_url:
        await ctx.info(f"Loaded complete documentation for '{project_name}' from {source_url}")
        return (
            f"[Complete documentation for '{project_name}' loaded from {source_url}."
            " No further calls needed.]\n\n" + content
        )
    await ctx.info(
        f"Loaded documentation index for '{project_name}' from {source_url}"
        " (llms-full.txt unavailable)"
    )
    return (
        f"[Documentation index for '{project_name}' from {source_url}."
        " Use read_doc_page with individual URLs to fetch content.]\n\n" + content
    )


@mcp.tool(title="Read Documentation Page", annotations=_READ_ONLY_WEB_TOOL)
async def read_doc_page(
    url: Annotated[
        str,
        Field(description="HTTPS URL of a documentation page from an llms.txt index."),
    ],
    ctx: Context,
) -> str:
    """Fetch the Markdown content of a specific documentation page.

    Use URLs obtained from ``get_project_docs`` when it returns an ``llms.txt``
    index. Do not call this if ``get_project_docs`` already returned complete
    documentation. Only URLs from configured project hosts are permitted.

    Args:
        url: HTTPS URL of the documentation page to fetch.
        ctx: MCP context used for protocol-level logging.

    Returns:
        Markdown content of the page, or an error message string if the URL
        is rejected or the fetch fails.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return f"Rejected: URL must use https (got '{parsed.scheme}')."
    if not parsed.hostname:
        return "Rejected: malformed URL or missing hostname."
    if parsed.hostname not in _ALLOWED_HOSTS:
        return (
            f"Rejected: host '{parsed.hostname}' is not from a configured project. "
            f"Allowed hosts: {', '.join(sorted(_ALLOWED_HOSTS))}."
        )
    await ctx.debug(f"Fetching page: {url}")
    await ctx.report_progress(0, None, f"Fetching {url}…")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, follow_redirects=True, timeout=10)
            response.raise_for_status()
            await ctx.report_progress(1, 1, "Page loaded")
            return response.text
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            await ctx.error(f"Failed to fetch {url}: {e}")
            raise


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("docshub://projects", mime_type="application/json")
def projects_resource() -> str:
    """Registry of all configured documentation projects.

    Returns a JSON array with ``name`` and ``description`` for each project.
    Use the exact ``name`` value when calling ``get_project_docs`` or reading
    the ``docshub://project/{name}/docs`` resource.

    Returns:
        JSON-encoded list of project dicts with ``name`` and ``description``.
    """
    return json.dumps(
        [
            {"name": name, "description": info.get("description", "")}
            for name, info in PROJECTS.items()
        ]
    )


@mcp.resource("docshub://project/{name}/docs", mime_type="text/plain")
async def project_docs_resource(name: str) -> str:
    """Raw documentation content for a named project.

    Returns the ``llms-full.txt`` content when available (complete
    documentation), otherwise the ``llms.txt`` index of URLs.

    Args:
        name: Project name as it appears in the ``docshub://projects`` registry.

    Returns:
        Raw documentation text, or an error message string if the project is
        not found or the fetch fails.
    """
    if name not in PROJECTS:
        return f"Project '{name}' not found. Read docshub://projects for valid names."
    try:
        content, _ = await _fetch_project_content(name)
        return content
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        return f"Failed to fetch documentation for '{name}': {e}"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def query_docs(project_name: str, question: str) -> str:
    """Answer a question using a project's documentation.

    Guides the model through the full lookup workflow: fetch docs, then answer.

    Args:
        project_name: Exact project name as returned by ``list_available_docs``.
        question: The question to answer using the project's documentation.

    Returns:
        A prompt string instructing the model to look up and answer the question.
    """
    return (
        f"Answer the following question using the '{project_name}' documentation "
        f"available through this MCP server:\n\n"
        f"Question: {question}\n\n"
        f"Workflow:\n"
        f"1. Call get_project_docs(project_name='{project_name}') to load the documentation.\n"
        f"2. If the response contains complete documentation, search it directly.\n"
        f"3. If the response is an index of URLs (llms.txt), identify the most relevant "
        f"pages and call read_doc_page for each.\n"
        f"4. Provide a clear, accurate answer grounded in the documentation."
    )


@mcp.prompt()
def summarize_project(project_name: str) -> str:
    """Summarize the documentation for a project.

    Guides the model to fetch and distil the key topics and structure.

    Args:
        project_name: Exact project name as returned by ``list_available_docs``.

    Returns:
        A prompt string instructing the model to fetch and summarize the docs.
    """
    return (
        f"Provide a structured overview of the '{project_name}' documentation "
        f"available through this MCP server:\n\n"
        f"Workflow:\n"
        f"1. Call get_project_docs(project_name='{project_name}') to load the documentation.\n"
        f"2. Summarise the main topics, key concepts, and overall structure.\n"
        f"3. Highlight the most important sections or areas covered."
    )


# ---------------------------------------------------------------------------
# Utilities: completion, logging, pagination
# ---------------------------------------------------------------------------

# MCP log level names → Python logging constants.
_MCP_LOG_LEVELS: dict[str, int] = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,  # Python has no NOTICE; map to INFO
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "alert": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


@mcp._mcp_server.set_logging_level()
async def handle_set_logging_level(level: str) -> None:
    """Handle client requests to change the server log level.

    Registering this handler causes FastMCP to declare the ``logging``
    capability in its ``ServerCapabilities``, enabling clients to discover
    and use it.

    Args:
        level: MCP log level name (e.g. ``"debug"``, ``"warning"``).
    """
    logging.getLogger("mcp").setLevel(_MCP_LOG_LEVELS.get(level, logging.WARNING))


@mcp.completion()
async def handle_completion(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,
) -> Completion | None:
    """Provide argument completions for prompts and resource templates.

    Registering this handler causes FastMCP to declare the ``completions``
    capability in its ``ServerCapabilities``.

    - Prompts (``query_docs``, ``summarize_project``): completes ``project_name``.
    - Resource template ``docshub://project/{name}/docs``: completes ``name``.

    Pagination: returns all matches in a single page (lists are small and
    bounded by the number of configured projects).

    Args:
        ref: Reference identifying the prompt or resource template being completed.
        argument: The argument being completed, including its current partial value.
        context: Optional completion context provided by the client.

    Returns:
        A ``Completion`` with matching project names, or ``None`` if the
        argument/ref combination is not handled.
    """
    if isinstance(ref, PromptReference) and argument.name == "project_name":
        partial = argument.value.lower()
        matches = [name for name in PROJECTS if partial in name.lower()]
        return Completion(values=matches, hasMore=False, total=len(matches))
    if isinstance(ref, ResourceTemplateReference) and argument.name == "name":
        partial = argument.value.lower()
        matches = [name for name in PROJECTS if partial in name.lower()]
        return Completion(values=matches, hasMore=False, total=len(matches))
    return None


def main() -> None:
    # MCP clients treat all stderr output as errors; suppress the mcp library's
    # INFO-level request logging so it doesn't produce false error reports.
    # Clients can raise this level at runtime via the 'logging' capability.
    logging.getLogger("mcp").setLevel(logging.WARNING)

    import anyio

    async def _run() -> None:
        task = asyncio.create_task(_config_watcher())
        try:
            await mcp.run_stdio_async()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    anyio.run(_run)


if __name__ == "__main__":
    main()
