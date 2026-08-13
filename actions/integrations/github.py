import requests

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "github_list_repos",
        "description": "Lists the authenticated user's GitHub repositories, most recently updated first.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "github_create_issue",
        "description": "Creates an issue in a GitHub repository.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "repo":  {"type": "STRING", "description": "owner/repo, e.g. 'octocat/Hello-World'"},
                "title": {"type": "STRING", "description": "Issue title"},
                "body":  {"type": "STRING", "description": "Issue body/description"},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "name": "github_search_repos",
        "description": "Searches public GitHub repositories.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Search query"}},
            "required": ["query"],
        },
    },
]


def _headers(creds: dict) -> dict:
    return {
        "Authorization": f"Bearer {creds['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("github")
    if not creds:
        return "GitHub isn't connected — add a Personal Access Token in the Integrations tab."

    try:
        if name == "github_list_repos":
            r = requests.get(
                "https://api.github.com/user/repos",
                headers=_headers(creds), params={"sort": "updated", "per_page": 15}, timeout=15,
            )
            r.raise_for_status()
            repos = r.json()
            if not repos:
                return "No repositories found."
            lines = [f"{repo['full_name']} ({'private' if repo['private'] else 'public'})" for repo in repos]
            return "Repositories:\n" + "\n".join(lines)

        if name == "github_create_issue":
            repo = args["repo"]
            payload = {"title": args["title"], "body": args.get("body", "")}
            r = requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers=_headers(creds), json=payload, timeout=15,
            )
            r.raise_for_status()
            issue = r.json()
            return f"Created issue #{issue['number']} in {repo}: {issue['html_url']}"

        if name == "github_search_repos":
            r = requests.get(
                "https://api.github.com/search/repositories",
                headers=_headers(creds), params={"q": args["query"], "per_page": 8}, timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items:
                return f"No repositories found for '{args['query']}'."
            lines = [f"{it['full_name']} — {it['stargazers_count']}★ — {it['html_url']}" for it in items]
            return "Search results:\n" + "\n".join(lines)

    except requests.HTTPError as e:
        msg = f"GitHub API error: {e.response.status_code} {e.response.text[:200]}"
        log("GitHub", msg, player)
        return msg
    except Exception as e:
        msg = f"GitHub error: {e}"
        log("GitHub", msg, player)
        return msg

    return "Unknown GitHub action."
