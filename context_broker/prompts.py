"""Extraction prompt template for Context Broker."""

EXTRACTION_PROMPT = """You are a context extraction engine. Analyze the following conversation transcript and extract every meaningful fact, decision, constraint, and implementation detail into a structured graph.

Return ONLY valid JSON matching this schema — no markdown fences, no commentary:

{
  "nodes": [
    {
      "id": "n1",
      "fact": "Clear, concise statement of the fact",
      "type": "implementation",
      "confidence": 0.95,
      "source_message": 12,
      "supersedes": [],
      "tags": ["keyword1", "keyword2"]
    }
  ],
  "edges": [
    {
      "from": "n1",
      "to": "n2",
      "relation": "depends_on"
    }
  ]
}

EXTRACTION RULES:
1. Extract FACTS, not conversation filler (greetings, confirmations, thinking-out-loud)
2. Each fact should be a self-contained statement understandable without surrounding context
3. When a decision is reversed or modified later in the conversation, create a new node with the updated decision and list the old node's id in "supersedes"
4. Tag nodes with relevant technical keywords for retrieval
5. "source_message" is the 0-based index of the message where the fact was established or decided
6. Confidence reflects how firmly established the fact is:
   - 0.3-0.5: mentioned/discussed but not decided
   - 0.6-0.8: decided but not implemented
   - 0.9-1.0: implemented/verified in the conversation
7. Node types:
   - "decision": a choice between alternatives
   - "constraint": a limitation or requirement
   - "implementation": a concrete technical detail
   - "question": an open question not yet resolved
   - "resolved": an answer to a previously open question
   - "preference": a stated preference for future work
8. Edge relations:
   - "depends_on": target is required for source to work
   - "flows_to": data or control flows from source to target
   - "relates_to": loosely related concepts
   - "supersedes": source replaces/overrides target

TRANSCRIPT:
{transcript}"""


def build_extraction_prompt(transcript_text: str) -> str:
    """Format the extraction prompt with the given transcript."""
    return EXTRACTION_PROMPT.format(transcript=transcript_text)
