"""LLM task queue — the Base44 AI bridge.

OpenAI is GONE (owner directive, Oct 16 2026): the worker cannot call
Base44's AI gateway directly (service-role only, from inside app
backend context — verified: external tokens get 401/403). So the
compute moves app-side through a queue:

  1. worker enqueues a prompt row on LLMTask (status=pending)
  2. an app-side function (scheduled automation, Base44 AI credits)
     reads pending rows, runs InvokeLLM / the gateway, writes back
     result_text + status=done
  3. worker collects done rows, validates, consumes them into the
     canonical chain (worker stays the ONLY intelligence writer)

Entity contract (created by the builder; until it exists every call
fails open and reads stay on the deterministic baseline):
  task_type    "research" | "synthesis"
  game_id      canonical Game id
  prompt       full baked-in prompt (all context inline)
  status       "pending" | "done" | "failed"
  result_text  model output text (done rows)
  error        failure note (failed rows)
  model        model name the app side used
  dedupe_key   "{task_type}:{game_id}"
  consumed     boolean — worker has folded it into the chain
"""

import json

from nova import config
from nova.api import Base44Client


ENTITY = config.LLM_QUEUE_ENTITY


class QueueUnavailable(Exception):
    """LLMTask entity missing (builder hasn't created it yet) or the
    API is refusing — everything fails open to the deterministic path."""


def _load(client, log):
    """All unconsumed + recent task rows (server-side filtering is
    dead on this API — verified 2026-10-13 — so filter client-side)."""
    try:
        rows = client.list_all(ENTITY) or []
    except Exception as e:                    # 404 entity, API error, ...
        raise QueueUnavailable(str(e)[:200])
    return rows


def enqueue(client, task_type, game_id, prompt, log=lambda *a: None):
    """Create a pending task unless an unconsumed one already exists.
    Returns the created row id, or None (dupe/unavailable)."""
    key = f"{task_type}:{game_id}"
    try:
        rows = _load(client, log)
    except QueueUnavailable as e:
        log(f"queue unavailable ({e}) — task not enqueued")
        return None
    for r in rows:
        if (r.get("dedupe_key") == key
                and r.get("status") in ("pending", "done")
                and not r.get("consumed")):
            return None                       # already in flight / awaiting pickup
    try:
        made = client.create(ENTITY, {
            "task_type": task_type,
            "game_id": game_id,
            "prompt": prompt,
            "status": "pending",
            "dedupe_key": key,
            "consumed": False,
        })
        if isinstance(made, dict) and made.get("id"):
            log(f"queued {key} ({len(prompt)} chars)")
            return made["id"]
        log(f"enqueue FAILED for {key} (create rejected) — "
            "check entity create permissions")
        return None
    except Exception as e:
        log(f"enqueue failed for {key} (non-fatal): {str(e)[:150]}")
        return None


def collect(client, task_type, game_id, log=lambda *a: None):
    """Return (task_id, result_text, model) for a finished, unconsumed
    task, or None. Never consumes — the caller does after parsing."""
    key = f"{task_type}:{game_id}"
    try:
        rows = _load(client, log)
    except QueueUnavailable as e:
        log(f"queue unavailable ({e}) — nothing to collect")
        return None
    for r in rows:
        if (r.get("dedupe_key") == key
                and r.get("status") == "done"
                and not r.get("consumed")
                and r.get("result_text")):
            return r.get("id"), r.get("result_text"), r.get("model") or "base44-ai"
    return None


def mark_consumed(client, task_id, log=lambda *a: None):
    """Fold a task into the chain so a fresh one can be enqueued later."""
    try:
        client.put(ENTITY, task_id, {"consumed": True})
    except Exception as e:
        log(f"mark_consumed failed for {task_id} (non-fatal): {str(e)[:150]}")


def sweep_failed(client, log=lambda *a: None):
    """Failed tasks unblock re-enqueue: mark consumed so dedupe lets a
    fresh attempt through next cycle."""
    try:
        rows = _load(client, log)
    except QueueUnavailable:
        return 0
    stale = [r["id"] for r in rows
             if r.get("status") == "failed" and not r.get("consumed")]
    if stale:
        try:
            client.bulk_put(ENTITY, [(i, {"consumed": True}) for i in stale])
            log(f"swept {len(stale)} failed tasks")
        except Exception as e:
            log(f"failed-task sweep error (non-fatal): {str(e)[:150]}")
    return len(stale)
