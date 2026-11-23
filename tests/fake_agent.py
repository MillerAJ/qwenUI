"""A stand-in for `qwen --acp` in tests. It speaks the same ACP messages Qwen Code 0.28.4 sends (shapes copied
from a real recorded session), without a model.

Prompts it understands:
  anything                  -> thinks, asks permission for a shell command, runs it (or not), answers with a chart
  text containing "slow"    -> streams slowly until cancelled (session/cancel -> stopReason "cancelled")
  text containing "plain"   -> just answers, no tools
"""
import json
import queue
import sys
import threading
import time

inbox: "queue.Queue[dict]" = queue.Queue()       # requests and notifications from the client
replies: "queue.Queue[dict]" = queue.Queue()     # the client's answers to our own requests
cancelled = threading.Event()
SID = "fake-session-1"
CONFIG = {
    "mode": {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": "default",
             "options": [{"value": v, "name": v.title()} for v in ("plan", "default", "auto-edit", "yolo")]},
    "model": {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": "fake-1",
              "options": [{"value": "fake-1", "name": "Fake Model 1"}]},
    "reasoning_effort": {"id": "reasoning_effort", "name": "Reasoning effort", "category": "thought_level",
                         "type": "select", "currentValue": "medium",
                         "options": [{"value": v, "name": v.title()} for v in ("none", "low", "medium", "xhigh")]},
}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def update(u, sid=SID):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})


def chunk(kind, text):
    update({"sessionUpdate": kind, "content": {"type": "text", "text": text}})


def reader():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("method") == "session/cancel":
            cancelled.set()
        elif "method" not in msg:
            replies.put(msg)
        else:
            inbox.put(msg)
    inbox.put({"method": "__eof__"})


def wait_response(rid):
    while True:
        msg = replies.get()
        if msg.get("id") == rid:
            return msg


def config_list():
    return list(CONFIG.values())


def do_prompt(rid, text):
    cancelled.clear()
    chunk("agent_message_chunk", "")                        # Qwen Code sends empty lifecycle chunks; ignore them
    if "slow" in text:
        for i in range(400):
            if cancelled.is_set():
                send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
                return
            chunk("agent_message_chunk", f"word{i} ")
            time.sleep(0.05)
    for piece in ("The user wants ", "a shell command run.\n"):
        chunk("agent_thought_chunk", piece)
    if "plain" not in text:
        tid = "call-1"
        update({"sessionUpdate": "tool_call", "toolCallId": tid, "status": "pending", "title": "Shell", "content": [],
                "locations": [], "kind": "execute", "rawInput": {}, "_meta": {"toolName": "run_shell_command"}})
        update({"sessionUpdate": "usage_update", "used": 14216, "size": 1000000})
        send({"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {
            "sessionId": SID,
            "options": [
                {"optionId": "proceed_always_project", "name": "Always Allow in project: touch *", "kind": "allow_always"},
                {"optionId": "proceed_always_user", "name": "Always Allow for user: touch *", "kind": "allow_always"},
                {"optionId": "proceed_once", "name": "Allow", "kind": "allow_once"},
                {"optionId": "cancel", "name": "Reject", "kind": "reject_once"}],
            "toolCall": {"toolCallId": tid, "status": "pending", "title": "touch probe.txt (Create a file.)",
                         "content": [], "locations": [], "kind": "execute",
                         "rawInput": {"command": "touch probe.txt", "description": "Create a file."}}}})
        answer = wait_response(0)
        picked = answer["result"]["outcome"].get("optionId")
        with open("permission-answer.txt", "w") as f:
            f.write(str(picked))
        if picked and picked != "cancel":
            update({"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "in_progress",
                    "title": "Shell: touch probe.txt", "kind": "execute", "rawInput": {"command": "touch probe.txt"}})
            update({"sessionUpdate": "tool_call_update", "toolCallId": tid, "_meta": {"toolLifecycle": {"phase": "ended"}}})
            update({"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "completed", "content": [
                {"type": "content", "content": {"type": "text", "text": "Command: touch probe.txt\nExit Code: 0"}}]})
        else:
            update({"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "failed", "content": [
                {"type": "content", "content": {"type": "text", "text": "Tool \"run_shell_command\" was canceled by the user."}}]})
        send({"jsonrpc": "2.0", "id": 1, "method": "craft/drainMidTurnQueue", "params": {"sessionId": SID}})
        wait_response(1)
    for piece in ("\n\nDone. ", "Here is the trend:\n\n```chart\n",
                  '{"type": "line", "title": "Trend", "x": [1, 2, 3], "y": [3, 5, 4]}\n', "```\n"):
        chunk("agent_message_chunk", piece)
    update({"sessionUpdate": "usage_update", "used": 14366, "size": 1000000})
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def main():
    threading.Thread(target=reader, daemon=True).start()
    while True:
        msg = inbox.get()
        m, rid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if m == "__eof__":
            return
        if m is None:
            continue                                     # a stray response
        if m == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentInfo": {"name": "fake-agent", "title": "Fake Agent", "version": "0.0.1"},
                "authMethods": [], "agentCapabilities": {"loadSession": True, "sessionCapabilities": {"list": {}, "resume": {}}}}})
        elif m == "session/new":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": SID, "configOptions": config_list()}})
        elif m == "session/set_config_option":
            CONFIG[p["configId"]]["currentValue"] = p["value"]
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": config_list()}})
        elif m == "session/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessions": [
                {"sessionId": "old-session-9", "title": "An earlier chat", "updatedAt": "2026-11-22T10:00:00Z", "cwd": p.get("cwd")}]}})
        elif m == "session/load":
            sid = p["sessionId"]
            update({"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "hello from before"}}, sid)
            update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "old thought"}}, sid)
            update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "old answer"}}, sid)
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": config_list()}})
        elif m == "session/prompt":
            text = " ".join(b.get("text", "") for b in p.get("prompt", []))
            threading.Thread(target=do_prompt, args=(rid, text), daemon=True).start()
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"{m} not supported"}})


if __name__ == "__main__":
    main()
