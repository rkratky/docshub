# DocsHub MCP Server

An MCP server that provides AI clients with access to developer documentation via `llms.txt` files. Exposes tools, resources, and prompts.

## Prerequisites

You must have [uv](https://docs.astral.sh/uv/) installed on your machine.
*(macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`)*

## Configuration

DocsHub loads its project list from a `docs_config.yaml` file. Sources are tried in this order:

1. **`DOCSHUB_CONFIG` environment variable** — set to a local file path *or* a remote `https://` URL pointing to a raw YAML file (e.g. a file hosted in a GitHub repository)
2. **`docs_config.yaml` in the working directory**
3. **`~/.config/docshub/docs_config.yaml`** — user-level config
4. **Bundled default** — the `docs_config.yaml` in the DocsHub repo (used automatically as a fallback when no other config is found)

The YAML format:

```yaml
projects:
  - name: "FastAPI"
    description: "FastAPI official documentation"
    llms_txt_url: "https://fastapi.tiangolo.com/llms.txt"
```

**Using a remote config from a GitHub repository:**

Set `DOCSHUB_CONFIG` to the raw file URL:

```
DOCSHUB_CONFIG=https://raw.githubusercontent.com/ORG/docshub/main/docs_config.yaml
```

You can pass this to any AI client as an environment variable in its MCP server configuration (see [Client Setup](#client-setup) below).

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCSHUB_CONFIG` | *(none)* | Local file path or `https://` URL to the config file. |
| `DOCSHUB_CONFIG_RELOAD_INTERVAL` | `900` | Seconds between config reload checks. The server polls this source in the background and notifies clients when the project list changes. Set to `0` to disable polling entirely. |
| `DOCSHUB_CACHE_TTL` | `1800` | Seconds to cache fetched documentation in memory. Subsequent tool calls within the TTL window are served instantly without a network round-trip. Set to `0` to disable caching. |

---

## Client Setup

### MCP server configuration

**Claude Desktop**, **VS Code Cline**, and **GitHub Copilot CLI** use the same JSON format. Add this block to the `mcpServers` object in each client's config file (see per-client instructions below):

```json
{
  "mcpServers": {
    "docshub": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/rkratky/docshub",
        "docshub"
      ]
    }
  }
}
```

To load a remote `docs_config.yaml`, add an `env` key:

```json
{
  "mcpServers": {
    "docshub": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/rkratky/docshub",
        "docshub"
      ],
      "env": {
        "DOCSHUB_CONFIG": "https://raw.githubusercontent.com/ORG/docshub/main/docs_config.yaml"
      }
    }
  }
}
```

---

### Claude Desktop

1. Open your configuration file:
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the configuration block above to the `mcpServers` object.
3. Completely quit and restart Claude Desktop.

---

### VS Code — Cline extension

1. Open VS Code and open the **Cline** extension sidebar.
2. Click the **MCP Servers** icon → **Configure MCP Servers** to open `cline_mcp_settings.json`.
3. Add the configuration block above to the `mcpServers` object.
4. Save. Cline automatically restarts the server.

---

### GitHub Copilot (VS Code)

GitHub Copilot in VS Code uses VS Code's native MCP configuration, which has a slightly different format.

**Option A — User settings** (available across all workspaces):

1. Open VS Code user settings: `Ctrl+,` → **Open Settings (JSON)** (top-right icon).
2. Add the following:

```json
{
  "mcp": {
    "servers": {
      "docshub": {
        "type": "stdio",
        "command": "uvx",
        "args": [
          "--from", "git+https://github.com/rkratky/docshub",
          "docshub"
        ]
      }
    }
  }
}
```

**Option B — Workspace settings** (scoped to a single project):

Create or edit `.vscode/mcp.json` in your project root:

```json
{
  "servers": {
    "docshub": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/rkratky/docshub",
        "docshub"
      ]
    }
  }
}
```

After saving, open **GitHub Copilot Chat** — the `docshub` tools will be available automatically.

---

### Claude Code

Run this command once to register the server at user scope:

```bash
claude mcp add --scope user docshub -- \
  uvx --from git+https://github.com/rkratky/docshub docshub
```

To use a remote `docs_config.yaml`, set the environment variable before running the command or add it to your shell profile:

```bash
export DOCSHUB_CONFIG=https://raw.githubusercontent.com/ORG/docshub/main/docs_config.yaml
```

---

### GitHub Copilot CLI

Edit `~/.copilot/mcp-config.json` (created automatically by the CLI the first time you run it; you can also create it manually) and add the [configuration block above](#mcp-server-configuration) to the `mcpServers` object.

To use a remote `docs_config.yaml`, add an `env` key to the server entry:

```json
{
  "mcpServers": {
    "docshub": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/rkratky/docshub",
        "docshub"
      ],
      "env": {
        "DOCSHUB_CONFIG": "https://raw.githubusercontent.com/ORG/docshub/main/docs_config.yaml"
      }
    }
  }
}
```

The config file location can be changed by setting the `COPILOT_HOME` environment variable.

---

## Usage

DocsHub exposes three types of MCP primitives: **tools** (called automatically by the AI), **resources** (attached to context on demand), and **prompts** (predefined conversation starters).

### Tools

The AI calls these automatically when you ask about documentation:

| Tool | Description |
|------|-------------|
| `list_available_docs` | Lists configured projects. Always called first to discover what's available. |
| `get_project_docs` | Fetches documentation for a project. Tries `llms-full.txt` first (complete docs); falls back to `llms.txt` (an index of page URLs) if unavailable. |
| `read_doc_page` | Fetches a specific page by URL. Only needed when `get_project_docs` returned an `llms.txt` index. |

Example: *"Check the docs for FastAPI and show me how to write a route."*

> **Performance note:** fetched documentation is cached in memory for 30 minutes by default (`DOCSHUB_CACHE_TTL`); set to `0` to disable caching. The server also polls the config source in the background every 15 minutes (`DOCSHUB_CONFIG_RELOAD_INTERVAL`) and notifies connected clients when the project list changes, so doc sets can be added or removed without restarting the server; set to `0` to disable polling.

### Resources

Resources provide structured access to the documentation registry and content. Attach them to your conversation context when you want to ground the AI in a specific project's docs.

| URI | Description |
|-----|-------------|
| `docshub://projects` | JSON list of all configured projects with names and descriptions. |
| `docshub://project/{name}/docs` | Raw documentation content for the named project. |

### Prompts

Prompts are predefined conversation starters for common documentation tasks. How to invoke them depends on your AI client (look for a prompt/slash-command picker or similar):

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `query_docs` | `project_name`, `question` | Answer a specific question using a project's documentation. |
| `summarize_project` | `project_name` | Produce a structured overview of a project's docs. |
