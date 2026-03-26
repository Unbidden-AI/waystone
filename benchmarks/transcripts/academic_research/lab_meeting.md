# academic_research / lab_meeting

Zara: Hi everyone. Thanks for coming. Today I'm presenting some preliminary results for my few-shot Named Entity Recognition project, focusing on prompt-tuning with in-context learning.

Prof. Huang: Excellent, Zara. Please go ahead. We're keen to see the progress.

Zara: So, the core idea is to leverage large language models, specifically Llama-2 7B, by designing effective prompts that include a few examples for in-context learning, and then using prompt-tuning to adapt the model for NER without full fine-tuning.

Omar: Zara, could you elaborate a bit on the prompt structure? Are you using a fixed template, or is it dynamically generated?

Zara: Good question, Omar. We're using a semi-fixed template. It starts with an instruction like "Extract entities from the following text based on the provided examples." Then, we append 8-16 few-shot examples, each with an input sentence and its corresponding entity annotations, followed by the target sentence for prediction.

Lily: And the base model is Llama-2 7B, right? Are you exploring other foundation models, or is that the primary one for now?

Zara: Yes, Llama-2 7B is our primary for these initial experiments due to its accessibility and performance. We might explore larger models like Mixtral later if we see a significant bottleneck.

Zara: Now, for the results. On the CoNLL-2003 English dataset, using a 16-shot setup, we achieved an F1 score of 82.3.

Prof. Huang: How does that compare to the current state-of-the-art on CoNLL-2003, Zara?

Zara: The SOTA for fully supervised methods on CoNLL-2003 is around 93.5 F1. So, there's still a significant gap, as expected for a few-shot approach.

Omar: That's a decent starting point for few-shot. How does it stack up against other few-shot NER methods like SetFit or even just basic ICL without prompt-tuning?

Zara: Compared to a vanilla ICL approach, our prompt-tuning method shows about a 4-point F1 improvement. Against SetFit, which is a strong baseline, we're still a couple of points behind, but SetFit often requires more labeled data for its initial training phase.

Lily: Have you done any error analysis yet? Are there specific entity types or contexts where it struggles more?

Zara: Yes, we've started. It tends to struggle with longer, more complex entities, especially those with nested structures. Ambiguous contexts, like "Apple" referring to the company versus the fruit, are also challenging, even with the few-shot examples.

Prof. Huang: Zara, given the few-shot nature, I'm particularly interested in its performance on low-resource languages. That's often where these methods shine. Have you looked into that?

Zara: That's actually one of its current weaknesses, Prof. Huang. While it performs reasonably well on English, applying it directly to low-resource languages without any cross-lingual transfer or specific language adaptation yields much lower scores, often below 60 F1.

Prof. Huang: That's a critical point. For the paper, we should definitely explore cross-lingual transfer. Perhaps using a multilingual LLM and then evaluating on datasets like XTREME or WikiANN for languages like Swahili or Bengali. That could really highlight the method's potential.

Zara: That's a great suggestion, Prof. Huang. I'll start looking into XTREME and WikiANN. Do you have any specific languages in mind for the initial experiments?

Prof. Huang: Let's target a few from different language families, maybe Spanish, Arabic, and Chinese, to show broad applicability.

Omar: What about the computational cost of the prompt-tuning itself? Is it significantly lighter than full fine-tuning?

Zara: Absolutely, Omar. Prompt-tuning only updates a small set of parameters, typically a few million, compared to the billions in the full model. This makes it much faster to train and requires significantly less GPU memory.

Lily: And just to confirm, for the CoNLL-2003 results, you mentioned 16-shot. Is that consistent across all your current experiments?

Zara: Yes, for these initial English results, it's consistently 16-shot. We're planning to run ablation studies on the number of shots soon.

Prof. Huang: Alright, Zara, this is good progress. We need to keep the ACL submission deadline in mind. It's coming up fast.

Zara: Yes, Prof. Huang. The main paper submission is May 15th.

Prof. Huang: So, for next steps, I'd like you to prioritize those cross-lingual experiments. Let's aim for some preliminary results on XTREME by next week. Also, refine the prompt design based on your error analysis.

Zara: Will do, Prof. Huang. I'll focus on the cross-lingual transfer and try to iterate on the prompt structure.

Omar: If you need any help with data preprocessing for the XTREME datasets, Zara, let me know. I've worked with them before.

Lily: And perhaps exploring different prompt-tuning strategies, like P-tuning v2 or Prefix-tuning, could also be beneficial down the line.

Prof. Huang: Good points, Omar and Lily. For now, Zara, let's get a solid baseline for the cross-lingual aspect. We can then expand. Keep me updated on your progress.

Zara: Thank you, Prof. Huang. And thanks, Omar and Lily, for the feedback and suggestions.
