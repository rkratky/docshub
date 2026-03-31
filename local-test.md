Testing your server locally before pushing it to GitHub is highly recommended. The official Model Context Protocol (MCP) Inspector is a fantastic, interactive web UI that lets you simulate an LLM calling your tools.

Here is exactly how to spin it up and test your `DocsHub` server.

### 1. Prerequisites
Since the official Inspector is a web-based tool built by Anthropic, it runs via Node.js. You will need:
* **Node.js & npm** installed on your machine (so you can run `npx` commands).
* Your local directory structured with your `pyproject.toml`, the `src/docshub/server.py` file, and a test `docs_config.yaml` file.

### 2. Run the Inspector
Open your terminal, navigate to the root directory of your project (where your `pyproject.toml` and `docs_config.yaml` live), and run this command:

```bash
npx @modelcontextprotocol/inspector uv run docshub
```

**What this command does:**
* `npx @modelcontextprotocol/inspector`: Downloads and starts the official MCP Inspector web app.
* `uv run docshub`: Tells the Inspector to launch your local Python server (using the `docshub` command we defined in your `pyproject.toml`) and connect to it via standard input/output (`stdio`).

### 3. Test Your Tools in the Browser
Once you run that command, the terminal will give you a local URL (usually `http://localhost:5173`). 

1.  Open that URL in your web browser.
2.  You will see the MCP Inspector interface. Click the **Connect** button to attach the UI to your running Python script.
3.  Click on the **Tools** tab in the sidebar. You should instantly see your three tools listed: `list_available_docs`, `get_project_directory`, and `read_doc_page`.
4.  **Execute a Tool:** Select `list_available_docs` and click "Run". You should see the output parsed directly from your `docs_config.yaml` file.
5.  **Test the Chain:** Copy a URL from that output, paste it into the `get_project_directory` tool, and run it to make sure it successfully fetches the remote `llms.txt` file.



If everything works and returns the Markdown you expect, you are perfectly clear to commit your code, push it to GitHub, and let your AI client take the wheel!
