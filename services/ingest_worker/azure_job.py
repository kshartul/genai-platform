"""Azure Container Apps job entry point: drain Event Grid BlobCreated messages from the storage
queue (managed identity, no keys) and ingest each blob. KEDA starts one execution per backlog."""

from __future__ import annotations

import base64
import json
import os

from .handlers import azure_handler


def main(max_messages: int = 32) -> None:
    from azure.identity import DefaultAzureCredential
    from azure.storage.queue import QueueClient

    account = os.environ["AZURE_STORAGE_ACCOUNT"]
    q = QueueClient(f"https://{account}.queue.core.windows.net", os.environ["AZURE_INGEST_QUEUE"],
                    credential=DefaultAzureCredential())
    for msg in q.receive_messages(messages_per_page=max_messages, visibility_timeout=1800):
        raw = msg.content
        try:
            event = json.loads(raw)
        except ValueError:
            event = json.loads(base64.b64decode(raw))  # Event Grid writes base64 JSON to storage queues
        azure_handler(event)
        q.delete_message(msg)


if __name__ == "__main__":
    main()
