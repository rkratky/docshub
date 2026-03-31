"""Unit tests for docshub.server.

Tests are organised by the function/feature they cover and follow the
Arrange-Act-Assert (AAA) pattern. All tests are fully isolated: no real
network calls are made and the module-level PROJECTS registry is patched
where needed.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp.types import (
    CompletionArgument,
    PromptReference,
    ResourceTemplateReference,
)

import docshub.server as srv
from docshub.server import (
    _BUNDLED_CONFIG,
    _cache_clear,
    _cache_get,
    _cache_set,
    _derive_full_txt_url,
    _fetch_project_content,
    _parse_projects,
    _reload_if_changed,
    get_project_docs,
    handle_completion,
    list_available_docs,
    load_config,
    mcp,
    read_doc_page,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PROJECTS = {
    "FastAPI": {
        "name": "FastAPI",
        "description": "FastAPI documentation",
        "llms_txt_url": "https://fastapi.tiangolo.com/llms.txt",
    },
    "Django": {
        "name": "Django",
        "description": "Django documentation",
        "llms_txt_url": "https://docs.djangoproject.com/llms.txt",
    },
}


def _make_ctx() -> MagicMock:
    """Return a mock MCP Context with async log methods."""
    ctx = MagicMock()
    ctx.debug = AsyncMock()
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.error = AsyncMock()
    ctx.report_progress = AsyncMock()
    ctx.elicit = AsyncMock(return_value=MagicMock(action="cancel", data=None))
    return ctx


# ---------------------------------------------------------------------------
# _parse_projects
# ---------------------------------------------------------------------------


class TestParseProjects:
    def test_returns_projects_list_from_valid_yaml(self) -> None:
        yaml_text = "projects:\n  - name: FastAPI\n    llms_txt_url: https://example.com/llms.txt"

        result = _parse_projects(yaml_text, "test")

        assert result == [{"name": "FastAPI", "llms_txt_url": "https://example.com/llms.txt"}]

    def test_returns_empty_list_when_projects_key_missing(self) -> None:
        yaml_text = "other_key: value"

        result = _parse_projects(yaml_text, "test")

        assert result == []

    def test_returns_empty_list_when_yaml_is_empty(self) -> None:
        result = _parse_projects("", "test")

        assert result == []

    def test_returns_empty_list_when_yaml_is_a_list(self) -> None:
        yaml_text = "- name: FastAPI"

        result = _parse_projects(yaml_text, "test")

        assert result == []

    def test_returns_empty_list_and_warns_on_malformed_yaml(self, capsys) -> None:
        malformed = "key: :\n  bad: [unclosed"

        result = _parse_projects(malformed, "my-source")

        assert result == []
        captured = capsys.readouterr()
        assert "my-source" in captured.err
        assert "Warning" in captured.err


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_reads_local_file_when_it_exists(self, tmp_path) -> None:
        config = tmp_path / "docs_config.yaml"
        config.write_text("projects:\n  - name: FastAPI\n    llms_txt_url: https://x.com/llms.txt")

        with patch("docshub.server._CONFIG_SEARCH_PATHS", [str(config)]):
            result = load_config()

        assert len(result) == 1
        assert result[0]["name"] == "FastAPI"

    def test_skips_missing_files_and_returns_empty_list(self, tmp_path) -> None:
        missing = str(tmp_path / "nonexistent.yaml")

        with patch("docshub.server._CONFIG_SEARCH_PATHS", [missing]):
            result = load_config()

        assert result == []

    def test_skips_empty_string_sources(self) -> None:
        with patch("docshub.server._CONFIG_SEARCH_PATHS", [""]):
            result = load_config()

        assert result == []

    def test_warns_and_skips_http_sources(self, capsys) -> None:
        with patch("docshub.server._CONFIG_SEARCH_PATHS", ["http://example.com/config.yaml"]):
            result = load_config()

        assert result == []
        captured = capsys.readouterr()
        assert "insecure" in captured.err

    def test_fetches_remote_https_config(self) -> None:
        yaml_body = "projects:\n  - name: Remote\n    llms_txt_url: https://x.com/llms.txt"
        mock_response = MagicMock()
        mock_response.text = yaml_body
        mock_response.raise_for_status = MagicMock()

        with patch("docshub.server._CONFIG_SEARCH_PATHS", ["https://example.com/config.yaml"]):
            with patch("httpx.get", return_value=mock_response) as mock_get:
                result = load_config()

        mock_get.assert_called_once()
        assert result[0]["name"] == "Remote"

    def test_warns_and_continues_on_remote_fetch_failure(self, capsys) -> None:
        with patch("docshub.server._CONFIG_SEARCH_PATHS", ["https://example.com/config.yaml"]):
            with patch("httpx.get", side_effect=httpx.RequestError("timeout")):
                result = load_config()

        assert result == []
        captured = capsys.readouterr()
        assert "Warning" in captured.err


# ---------------------------------------------------------------------------
# _derive_full_txt_url
# ---------------------------------------------------------------------------


class TestDeriveFullTxtUrl:
    def test_replaces_llms_txt_suffix(self) -> None:
        url = "https://example.com/llms.txt"
        assert _derive_full_txt_url(url) == "https://example.com/llms-full.txt"

    def test_returns_empty_string_when_suffix_absent(self) -> None:
        url = "https://example.com/docs.txt"
        assert _derive_full_txt_url(url) == ""

    def test_handles_url_with_path_prefix(self) -> None:
        url = "https://example.com/en/stable/llms.txt"
        assert _derive_full_txt_url(url) == "https://example.com/en/stable/llms-full.txt"


# ---------------------------------------------------------------------------
# _fetch_project_content
# ---------------------------------------------------------------------------


class TestFetchProjectContent:
    @pytest.fixture(autouse=True)
    def _patch_projects(self):
        with patch("docshub.server.PROJECTS", _SAMPLE_PROJECTS):
            yield

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _cache_clear()
        yield
        _cache_clear()

    async def test_returns_full_txt_when_available(self) -> None:
        full_response = MagicMock(text="full docs", raise_for_status=MagicMock())
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=full_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            content, url = await _fetch_project_content("FastAPI")

        assert content == "full docs"
        assert url.endswith("llms-full.txt")

    async def test_falls_back_to_llms_txt_on_404(self) -> None:
        index_response = MagicMock(text="index docs", raise_for_status=MagicMock())
        mock_client = AsyncMock()

        async def fake_get(url, **kwargs):
            if "llms-full.txt" in url:
                raise httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
            return index_response

        mock_client.get = fake_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            content, url = await _fetch_project_content("FastAPI")

        assert content == "index docs"
        assert url.endswith("llms.txt")

    async def test_raises_on_network_error_for_both_urls(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.RequestError):
                await _fetch_project_content("FastAPI")


# ---------------------------------------------------------------------------
# list_available_docs
# ---------------------------------------------------------------------------


class TestListAvailableDocs:
    def test_returns_name_and_description_for_each_project(self) -> None:
        with patch("docshub.server.PROJECTS", _SAMPLE_PROJECTS):
            result = list_available_docs()

        assert len(result) == 2
        assert {"name": "FastAPI", "description": "FastAPI documentation"} in result
        assert {"name": "Django", "description": "Django documentation"} in result

    def test_returns_empty_list_when_no_projects(self) -> None:
        with patch("docshub.server.PROJECTS", {}):
            result = list_available_docs()

        assert result == []


# ---------------------------------------------------------------------------
# get_project_docs
# ---------------------------------------------------------------------------


class TestGetProjectDocs:
    @pytest.fixture(autouse=True)
    def _patch_projects(self):
        with patch("docshub.server.PROJECTS", _SAMPLE_PROJECTS):
            yield

    async def test_returns_error_for_unknown_project(self) -> None:
        ctx = _make_ctx()

        result = await get_project_docs("Unknown", ctx)

        assert "not found" in result.lower()

    async def test_returns_complete_docs_header_for_full_txt(self) -> None:
        ctx = _make_ctx()
        full_url = "https://fastapi.tiangolo.com/llms-full.txt"

        with patch(
            "docshub.server._fetch_project_content",
            AsyncMock(return_value=("full content", full_url)),
        ):
            with patch("docshub.server._derive_full_txt_url", return_value=full_url):
                result = await get_project_docs("FastAPI", ctx)

        assert "Complete documentation" in result
        assert "full content" in result
        ctx.info.assert_awaited_once()

    async def test_returns_index_header_for_llms_txt_fallback(self) -> None:
        ctx = _make_ctx()
        index_url = "https://fastapi.tiangolo.com/llms.txt"

        with patch(
            "docshub.server._fetch_project_content",
            AsyncMock(return_value=("index content", index_url)),
        ):
            with patch(
                "docshub.server._derive_full_txt_url",
                return_value="https://fastapi.tiangolo.com/llms-full.txt",
            ):
                result = await get_project_docs("FastAPI", ctx)

        assert "Documentation index" in result
        assert "index content" in result

    async def test_returns_error_message_on_fetch_failure(self) -> None:
        ctx = _make_ctx()

        with patch(
            "docshub.server._fetch_project_content",
            AsyncMock(side_effect=httpx.RequestError("timeout")),
        ):
            with pytest.raises(httpx.RequestError):
                await get_project_docs("FastAPI", ctx)

        ctx.error.assert_awaited_once()


# ---------------------------------------------------------------------------
# read_doc_page
# ---------------------------------------------------------------------------


class TestReadDocPage:
    @pytest.fixture(autouse=True)
    def _patch_allowed_hosts(self):
        allowed = frozenset({"fastapi.tiangolo.com", "docs.djangoproject.com"})
        with patch("docshub.server._ALLOWED_HOSTS", allowed):
            yield

    async def test_rejects_non_https_urls(self) -> None:
        ctx = _make_ctx()

        result = await read_doc_page("http://fastapi.tiangolo.com/page", ctx)

        assert "Rejected" in result
        assert "https" in result

    async def test_rejects_malformed_url_with_no_hostname(self) -> None:
        ctx = _make_ctx()

        result = await read_doc_page("https://", ctx)

        assert "Rejected" in result
        assert "hostname" in result

    async def test_rejects_url_from_non_configured_host(self) -> None:
        ctx = _make_ctx()

        result = await read_doc_page("https://evil.example.com/page", ctx)

        assert "Rejected" in result
        assert "evil.example.com" in result

    async def test_returns_page_content_for_valid_url(self) -> None:
        ctx = _make_ctx()
        page_content = "# FastAPI docs\nContent here."
        mock_response = MagicMock(text=page_content, raise_for_status=MagicMock())
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await read_doc_page("https://fastapi.tiangolo.com/docs/page", ctx)

        assert result == page_content
        ctx.debug.assert_awaited_once()

    async def test_returns_error_message_on_fetch_failure(self) -> None:
        ctx = _make_ctx()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.RequestError):
                await read_doc_page("https://fastapi.tiangolo.com/docs/page", ctx)

        ctx.error.assert_awaited_once()


# ---------------------------------------------------------------------------
# handle_completion
# ---------------------------------------------------------------------------


class TestHandleCompletion:
    @pytest.fixture(autouse=True)
    def _patch_projects(self):
        with patch("docshub.server.PROJECTS", _SAMPLE_PROJECTS):
            yield

    async def test_completes_project_name_for_prompt_reference(self) -> None:
        ref = PromptReference(type="ref/prompt", name="query_docs")
        arg = CompletionArgument(name="project_name", value="Fast")

        result = await handle_completion(ref, arg, None)

        assert result is not None
        assert result.values == ["FastAPI"]

    async def test_returns_all_projects_for_empty_prefix(self) -> None:
        ref = PromptReference(type="ref/prompt", name="summarize_project")
        arg = CompletionArgument(name="project_name", value="")

        result = await handle_completion(ref, arg, None)

        assert result is not None
        assert set(result.values) == {"FastAPI", "Django"}

    async def test_completes_name_for_resource_template_reference(self) -> None:
        ref = ResourceTemplateReference(type="ref/resource", uri="docshub://project/{name}/docs")
        arg = CompletionArgument(name="name", value="Dj")

        result = await handle_completion(ref, arg, None)

        assert result is not None
        assert result.values == ["Django"]

    async def test_returns_none_for_unhandled_argument_on_prompt(self) -> None:
        ref = PromptReference(type="ref/prompt", name="query_docs")
        arg = CompletionArgument(name="question", value="How")

        result = await handle_completion(ref, arg, None)

        assert result is None

    async def test_returns_none_for_project_name_on_resource_template(self) -> None:
        ref = ResourceTemplateReference(type="ref/resource", uri="docshub://project/{name}/docs")
        arg = CompletionArgument(name="project_name", value="Fast")

        result = await handle_completion(ref, arg, None)

        assert result is None

    async def test_completion_is_case_insensitive(self) -> None:
        ref = PromptReference(type="ref/prompt", name="query_docs")
        arg = CompletionArgument(name="project_name", value="fast")

        result = await handle_completion(ref, arg, None)

        assert result is not None
        assert "FastAPI" in result.values


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCaching:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("docshub.server.PROJECTS", _SAMPLE_PROJECTS):
            _cache_clear()
            yield
            _cache_clear()

    async def test_returns_cached_result_on_second_call(self) -> None:
        fetch_mock = AsyncMock(
            return_value=("cached docs", "https://fastapi.tiangolo.com/llms-full.txt")
        )

        with patch("docshub.server._fetch_uncached", fetch_mock):
            first = await _fetch_project_content("FastAPI")
            second = await _fetch_project_content("FastAPI")

        assert first == second
        fetch_mock.assert_awaited_once()

    async def test_cache_is_bypassed_after_expiry(self) -> None:
        _cache_set("FastAPI", "stale", "https://fastapi.tiangolo.com/llms.txt")
        # Expire the entry
        srv._docs_cache["FastAPI"].expires_at = time.monotonic() - 1

        fresh_mock = AsyncMock(return_value=("fresh docs", "https://fastapi.tiangolo.com/llms.txt"))
        with patch("docshub.server._fetch_uncached", fresh_mock):
            content, _ = await _fetch_project_content("FastAPI")

        assert content == "fresh docs"
        fresh_mock.assert_awaited_once()

    def test_cache_clear_empties_cache(self) -> None:
        _cache_set("FastAPI", "some content", "https://fastapi.tiangolo.com/llms.txt")
        assert _cache_get("FastAPI") is not None

        _cache_clear()

        assert _cache_get("FastAPI") is None


# ---------------------------------------------------------------------------
# Hot-reload (_reload_if_changed)
# ---------------------------------------------------------------------------


class TestReloadIfChanged:
    @pytest.fixture(autouse=True)
    def _reset_state(self):
        original_projects = srv.PROJECTS.copy()
        original_source = srv._config_source
        original_hash = srv._config_hash
        _cache_clear()
        yield
        srv.PROJECTS = original_projects
        srv._config_source = original_source
        srv._config_hash = original_hash
        _cache_clear()

    async def test_returns_false_when_content_unchanged(self, tmp_path) -> None:
        config_text = (
            "projects:\n  - name: FastAPI\n    llms_txt_url: https://fastapi.tiangolo.com/llms.txt"
        )
        config_file = tmp_path / "docs_config.yaml"
        config_file.write_text(config_text)

        srv._config_source = str(config_file)
        from docshub.server import _hash_text

        srv._config_hash = _hash_text(config_text)

        result = await _reload_if_changed()

        assert result is False

    async def test_returns_true_and_updates_projects_when_content_changed(self, tmp_path) -> None:
        old_text = (
            "projects:\n  - name: FastAPI\n    llms_txt_url: https://fastapi.tiangolo.com/llms.txt"
        )
        new_text = (
            "projects:\n  - name: NewProject\n    llms_txt_url: https://new.example.com/llms.txt"
        )
        config_file = tmp_path / "docs_config.yaml"
        config_file.write_text(new_text)

        srv._config_source = str(config_file)
        from docshub.server import _hash_text

        srv._config_hash = _hash_text(old_text)  # Different from file content

        result = await _reload_if_changed()

        assert result is True
        assert "NewProject" in srv.PROJECTS

    async def test_skips_bundled_config(self) -> None:
        srv._config_source = str(_BUNDLED_CONFIG)

        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await _reload_if_changed()

        assert result is False
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Tool annotations
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    def test_all_tools_have_read_only_hint(self) -> None:
        tools = {t.name: t for t in mcp._tool_manager.list_tools()}

        for tool_name in ("list_available_docs", "get_project_docs", "read_doc_page"):
            assert tools[tool_name].annotations is not None, f"{tool_name} missing annotations"
            assert (
                tools[tool_name].annotations.readOnlyHint is True
            ), f"{tool_name} should have readOnlyHint=True"
