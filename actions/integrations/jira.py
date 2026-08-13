import requests
from requests.auth import HTTPBasicAuth

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "jira_search_issues",
        "description": "Searches Jira issues using JQL (Jira Query Language) or plain text.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "JQL query, e.g. 'assignee=currentUser() AND status=\"To Do\"', or plain text to search summaries"}},
            "required": ["query"],
        },
    },
    {
        "name": "jira_create_issue",
        "description": "Creates a Jira issue.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "project_key": {"type": "STRING", "description": "Jira project key, e.g. 'ENG'"},
                "summary":     {"type": "STRING", "description": "Issue summary/title"},
                "description": {"type": "STRING", "description": "Issue description"},
                "issue_type":  {"type": "STRING", "description": "Issue type, e.g. 'Task', 'Bug' (default: Task)"},
            },
            "required": ["project_key", "summary"],
        },
    },
]


def _auth(creds: dict) -> HTTPBasicAuth:
    return HTTPBasicAuth(creds["email"], creds["api_token"])


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("jira")
    if not creds:
        return "Jira isn't connected — add your site URL, email, and API token in the Integrations tab."
    base = f"https://{creds['site_url'].replace('https://', '').rstrip('/')}"

    try:
        if name == "jira_search_issues":
            q = args["query"]
            jql = q if any(k in q for k in ("=", "~", "AND", "OR")) else f'text ~ "{q}"'
            r = requests.get(
                f"{base}/rest/api/3/search", auth=_auth(creds),
                params={"jql": jql, "maxResults": 10}, timeout=15,
            )
            r.raise_for_status()
            issues = r.json().get("issues", [])
            if not issues:
                return "No matching issues found."
            lines = [f"{i['key']}: {i['fields']['summary']} ({i['fields']['status']['name']})" for i in issues]
            return "Issues:\n" + "\n".join(lines)

        if name == "jira_create_issue":
            payload = {
                "fields": {
                    "project": {"key": args["project_key"]},
                    "summary": args["summary"],
                    "issuetype": {"name": args.get("issue_type", "Task")},
                }
            }
            if args.get("description"):
                payload["fields"]["description"] = {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": args["description"]}]}],
                }
            r = requests.post(f"{base}/rest/api/3/issue", auth=_auth(creds), json=payload, timeout=15)
            r.raise_for_status()
            key = r.json()["key"]
            return f"Created Jira issue {key}: {base}/browse/{key}"

    except requests.HTTPError as e:
        msg = f"Jira API error: {e.response.status_code} {e.response.text[:200]}"
        log("Jira", msg, player)
        return msg
    except Exception as e:
        msg = f"Jira error: {e}"
        log("Jira", msg, player)
        return msg

    return "Unknown Jira action."
