// Read injected URL/key unchanged: with proxy enabled these are temporary run credentials.
// Do not hardcode upstream credentials or print secrets. Local calls use your own upstream account.
// Run only the example supported by your provider; Responses is not universally available.

import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
  baseURL: process.env.OPENAI_BASE_URL,
});

// Choose a model supported by the submitted API endpoint and key.
const model = "your-model-name";

export async function chatCompletionsExample(prompt: string): Promise<string> {
  const response = await client.chat.completions.create({
    model,
    messages: [
      { role: "system", content: "You are a coding agent." },
      { role: "user", content: prompt },
    ],
  });
  return response.choices?.[0]?.message?.content || "";
}

export async function responsesExample(prompt: string): Promise<string> {
  const response = await client.responses.create({
    model,
    instructions: "You are a coding agent.",
    input: [{ role: "user", content: prompt }],
  });
  return response.output_text || "";
}
