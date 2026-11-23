from __future__ import annotations

# Read injected URL/key unchanged: with proxy enabled these are temporary run credentials.
# Do not hardcode upstream credentials or print secrets. Local calls use your own upstream account.
# Run only the example supported by your provider; Responses is not universally available.

import os
from typing import Any

from openai import OpenAI

def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    # Select a model supported by your endpoint and key in your agent code.
    model = "your-model-name"

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    
    # Example 1: usage of the OpenAI client to create a chat completion
    chat = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"}
        ]
    )
    print(chat.choices[0].message.content)
    
    # Example 2: usage of the OpenAI client to create a response
    response = client.responses.create(
        model=model,
        instructions="You are a helpful assistant.",
        input=[
            {
                "role": "user",
                "content": "What is the capital of France?"
            }
        ],
        stream=False,
    )
    print(response.output_text)
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
