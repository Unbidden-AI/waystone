# Conversation / Transcript Data Sources

Public datasets and synthetic generation guidance for each Waystone domain profile.
Compiled March 2026.

---

## medical_clinical

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| MedDialog (English) | `UCSD26/medical_dialog` | 0.26M dialogues | Doctor-patient conversations from healthcaremagic.com, icliniq.com |
| MedDialog (Chinese) | `UCSD26/medical_dialog` | 1.1M dialogues | 29 specialties, 172 fine-grained categories |
| BigBio MedDialog | `bigbio/meddialog` | Combined EN/CN | Unified biomedical NLP schema |
| NoteChat | `AGBonnet/augmented-clinical-notes` | 167K dialogues | Synthetic patient-doctor conversations from clinical notes (GPT-3.5) |
| MTS-Dialog | `har1/MTS_Dialogue-Clinical_Note` | 1.7K conversations | Doctor-patient dialogues paired with clinical note summaries |

**Synthetic generation approach:** Multi-turn anamnesis-style prompts mirroring how doctors gather patient history. Inject ICD-10 codes as scenario seeds. SCALEMED framework generated 1.2M samples from 50K self-instruct prompts + 72K knowledge-based tasks.

---

## legal

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| Supreme Court Oral Arguments | `convokit` / walkerdb/supreme_court_transcripts | 8,300 arguments, 1.8M utterances | US Supreme Court oral arguments through 2023 |
| Pile of Law | `pile-of-law/pile-of-law` | Large | Legal/administrative corpus for LLM pretraining |
| CUAD | `theatticusproject/atticus` | 510 contracts, 13K+ labels | 41 clause types labeled in commercial contracts |
| ContractNLI | Stanford NLP | — | Document-level NLI for contract review |
| Material Contracts Corpus | Stanford Law (2025) | 1M+ SEC contracts | SEC-filed contracts 2000-2023 |

**Synthetic generation approach:** Two-agent (Buyer vs. Seller) negotiation with conflicting goals. Template: contract type → contested clause → negotiating positions → resolution. Deposition transcripts are not public records — synthesize from court filings and published oral arguments only.

---

## meeting_notes

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| MeetingBank | meetingbank.github.io | 1,366 meetings, 3,579 hrs | US city council meetings; avg 28K tokens/transcript |
| AMI Corpus | `knkarthick/AMI` | 100 hrs, 279 meetings | Scenario-driven business meetings; rich annotations (dialogue acts, summaries, entities) |

**Synthetic generation approach:** Agenda-driven with role-assigned speakers. Provide agenda + action items → generate conversation where these emerge naturally. Standup template: each participant reports done/doing/blockers in rapid-fire format.

---

## academic_research

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| Re2 Dataset | OpenReview | 19.9K submissions, 70.6K reviews | Papers + reviewer comments from 24 conferences (2017-2025); includes rebuttals |
| ICLR Peer Review Corpus | OpenReview | 19K+ papers | ICLR 2024-2025; author rebuttals with reviewer discussions |
| PeerRead | `allenai/peer_read` | 14.7K papers, 10.7K reviews | ACL/NIPS/ICLR reviews with acceptance decisions |

**Synthetic generation approach:** Extract paper abstract/methods/results → generate structured review (Strengths / Weaknesses / Questions / Verdict). Reviewers must cite specific sections. Author rebuttal → reviewer response cycles.

---

## financial

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| S&P 500 Earnings Transcripts | `kurry/sp500_earnings_transcripts` | 33K+ transcripts | 685 companies, 2005-2025; speaker-by-speaker structure |
| Earnings Call Q&A | `lamini/earnings-calls-qa` | — | Structured Q&A from earnings calls |
| IBM Earnings | `jlh-ibm/earnings_call` | 188 transcripts | Paired with stock prices and sector indices |
| finosfoundation Transcripts | `finosfoundation/EarningsCallTranscript` | — | Audio + Mistral Voxtral transcriptions |

**Synthetic generation approach:** CEO/CFO opening remarks (overview + guidance) → analyst Q&A (margins, CapEx, outlook). Numbers must be internally consistent. Forward guidance within ±30% of historical ranges for sector.

---

## episodic_personal

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| LoCoMo | snap-research/locomo | 300-turn avg, 35 sessions | Long-term conversations with event graphs; spans 6-12 simulated months — **primary benchmark** |
| MSC (Multi-Session Chat) | ParlAI projects/msc | Multiple sessions | Two speakers build shared history across sessions |
| DREAM | dataset.org/dream | 6.4K dialogues | Daily-life scenario dialogues with comprehension Q&A |

---

## customer_support

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| syncora Customer Support | `syncora/customer_support_conversations_dataset` | Synthetic | Privacy-safe; MIT License; intent/sentiment classification |
| Bitext Customer Support | github.com/bitext/customer-support-llm-chatbot-training-dataset | 3.57M tokens | LLM fine-tuning data for conversational customer service |
| Tech Support Conversations | Kaggle/steve1215rogg | — | Real tech support interactions |

**Synthetic generation approach:** Convert ticket (category + description) into multi-turn dialogue. Escalation path: L1 attempt → objection → L2 escalation. Customer sentiment should improve by end.

---

## education_tutoring

| Dataset | HuggingFace ID / Source | Size | Notes |
|---|---|---|---|
| Google Education Dialogue | google-research-datasets/Education-Dialogue-Dataset | — | Generated with Gemini Ultra; teacher-student pairs |
| MathDial | OpenReview | 3K dialogues | One-to-one tutoring on multi-step math reasoning |
| Saga Dataset | EDM 2025 | 121 sessions, 69.7 hrs | 33.6K teacher + 11.1K student utterances; pedagogical talk moves labeled |
| StudyChat | EDM 2025 | 1.2K conversations | Real student interactions with LLM tutor in university course |

**Synthetic generation approach:** Scaffolding structure — ask leading question → student attempts → provide hint → student revises. Inject specific misconceptions and have teacher guide student to self-correct. Include named pedagogical moves (open questions, revoicing, wait time).

---

## news_events

No single authoritative benchmark dataset exists. Best approaches:
- Use real news transcripts from NPR, BBC Sounds, or C-SPAN (publicly available)
- **Wikinews** for structured event reporting
- **CommonCrawl News** for raw news text at scale
- Synthetic: anchor-reporter dialogue format covering a single event with multiple stakeholder perspectives

---

## Quality Evaluation Metrics

| Metric | Use Case | Target |
|---|---|---|
| BLEU | Lexical overlap vs. reference | > 0.3 |
| ROUGE-L | Abstractive summary recall | > 0.4 |
| BERTScore | Contextual embedding similarity | Correlates better with human judgment than BLEU |
| Distinct-1/2 | Lexical diversity | Distinct-1 > 0.5, Distinct-2 > 0.2 |
| G-Eval | LLM-based multi-criteria (coherence, relevance, fluency) | > 3.5/5; Spearman corr 0.66+ |

For medical, legal, finance: domain expert spot-checks on 5-10% of synthetic data are essential.
