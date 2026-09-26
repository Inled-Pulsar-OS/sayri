"""Sayri Reinforcement & Preference Learning CLI tool."""

import argparse
import sys
from typing import List, Optional

from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository


def format_query_results(results: List[dict]) -> str:
    if not results:
        return "No learned preference found."
    lines = ["Found learned preference(s):"]
    for idx, r in enumerate(results, 1):
        score_str = f"+{r['score']}" if r['score'] > 0 else f"{r['score']}"
        rej_str = f" [Avoid: {r['rejected_command']}]" if r.get("rejected_command") else ""
        lines.append(f"{idx}. Intent: '{r['intent']}' -> Command: `{r['command']}` (Score: {score_str}, Success: {r['success_count']}, Fail: {r['failure_count']}){rej_str}")
    return "\n".join(lines)


def main(args: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sayri Agent Reinforcement & Preference Memory CLI")
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to run")

    # query
    query_parser = subparsers.add_parser("query", help="Query learned preferences by intent or keyword")
    query_parser.add_argument("query_str", type=str, help="Search query (intent, keywords, or app name)")
    query_parser.add_argument("--agent", type=str, default=None, help="Optional agent ID")
    query_parser.add_argument("--limit", type=int, default=3, help="Max results to return")

    # record
    rec_parser = subparsers.add_parser("record", help="Record a learned preference trajectory")
    rec_parser.add_argument("--intent", required=True, type=str, help="User intent or action description")
    rec_parser.add_argument("--command", required=True, type=str, help="Successful or preferred bash command")
    rec_parser.add_argument("--rejected", type=str, default=None, help="Rejected or incorrect command")
    rec_parser.add_argument("--agent", type=str, default="default", help="Agent ID")
    rec_parser.add_argument("--fail", action="store_true", help="Record as failure rather than success")
    rec_parser.add_argument("--notes", type=str, default="", help="Optional notes")

    # list
    list_parser = subparsers.add_parser("list", help="List all learned preferences")
    list_parser.add_argument("--agent", type=str, default=None, help="Optional agent ID")
    list_parser.add_argument("--limit", type=int, default=50, help="Max items")

    # clear
    clear_parser = subparsers.add_parser("clear", help="Clear learned preferences")
    clear_parser.add_argument("--agent", type=str, default=None, help="Optional agent ID to clear")

    raw_args = args if args is not None else sys.argv[1:]
    if raw_args and raw_args[0] not in ("query", "record", "list", "clear", "-h", "--help"):
        # Convenience: sayri-pref "intent words" defaults to query
        q_str = " ".join(raw_args)
        repo = SQLiteSessionRepository()
        results = repo.query_preferences(query=q_str, limit=3)
        print(format_query_results(results))
        return 0 if results else 1

    parsed = parser.parse_args(raw_args)
    repo = SQLiteSessionRepository()

    if parsed.subcommand == "query":
        results = repo.query_preferences(query=parsed.query_str, agent_id=parsed.agent, limit=parsed.limit)
        print(format_query_results(results))
        return 0 if results else 1

    elif parsed.subcommand == "record":
        pref_id = repo.record_preference(
            agent_id=parsed.agent,
            intent=parsed.intent,
            command=parsed.command,
            success=not parsed.fail,
            rejected_command=parsed.rejected,
            notes=parsed.notes,
        )
        print(f"Recorded preference ID {pref_id} for agent '{parsed.agent}'.")
        return 0

    elif parsed.subcommand == "list":
        items = repo.list_preferences(agent_id=parsed.agent, limit=parsed.limit)
        if not items:
            print("No preferences recorded yet.")
            return 0
        print(f"Recorded preferences ({len(items)}):")
        for it in items:
            score_str = f"+{it['score']}" if it['score'] > 0 else f"{it['score']}"
            rej = f" (Avoid: {it['rejected_command']})" if it.get("rejected_command") else ""
            print(f"[{it['id']}] [{it['agent_id']}] '{it['intent']}' -> `{it['command']}` [Score: {score_str}]{rej}")
        return 0

    elif parsed.subcommand == "clear":
        count = repo.clear_preferences(agent_id=parsed.agent)
        print(f"Cleared {count} preference record(s).")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
