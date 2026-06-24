---
name: unknown-domain-protocol
description: Use when encountering an unknown technology, protocol, standard, hardware component, or byte sequence not in the agent's knowledge base. All agents inherit this protocol — no agent is allowed to say "I don't know" and stop.
version: "1.0.0"
---
# Unknown Domain Protocol
## What every agent does when it encounters something it doesn't know

---

## The Rule

**No agent is allowed to say "I don't know" and stop.**

If an agent encounters a technology, protocol, standard, hardware component, byte sequence,
or behavior that is not in its knowledge base, it MUST follow this protocol.

This protocol is inherited by ALL agents in the system.

---

## Decision Tree

```
Agent encounters unknown X
        │
        ▼
Is X a name that looks like a standard or specification?
(e.g., "ISO XXXXX", "RFC XXXX", "IEEE 802.XX", "ETSI", "IETF", "ITU-T")
        │
   YES  │  NO
        │   └─► Go to: Section B (Research First)
        ▼
Section A: Known Standard Space
  → Look up the standard by name
  → Download or fetch the relevant RFC/ISO abstract
  → Extract: purpose, packet format, state machine, error codes
  → Document findings in knowledge/[domain]/[standard-name].md
  → Continue analysis with new knowledge

Is X completely undocumented / proprietary / unknown?
        │
   YES  └─► Go to: Section C (Scientific Experimentation)
```

---

## Section A — Fetch Known Standard

When the technology has a known standards body (ISO, IETF, IEEE, ETSI, NFC Forum, etc.):

1. **Search** for the standard:
   - RFC: `https://www.rfc-editor.org/rfc/rfcXXXX`
   - ISO: Search "ISO XXXXX filetype:pdf" or standards.iso.org
   - NFC Forum: nfc-forum.org/specs
   - USB: usb.org/documents
   - Bluetooth: bluetooth.com/specifications
   - IEEE: ieeexplore.ieee.org

2. **Extract** the following from the document:
   - Purpose and scope (1 paragraph)
   - Protocol layers involved
   - Frame/packet format (reproduce as ASCII table)
   - State machine (reproduce as ASCII diagram)
   - Command/response pairs
   - Error codes and their meanings
   - Timing constraints
   - Security considerations

3. **Save** to `knowledge/[domain]/[standard-name].md` using the standard knowledge template (see below)

4. **Link** the new knowledge file from the relevant agent's skill file

5. **Continue** analysis with the new knowledge applied

---

## Section B — Research First (Unknown Name, Possibly Standard)

When you encounter something like `FELICA`, `MIFARE`, `DESFIRE`, `EMV`, `NFC-F`, `PN532`, `RC522`:

1. **Web search** for: `"[name]" protocol specification OR datasheet OR technical reference`
2. **Check** manufacturer datasheets (NXP, ST, TI, Broadcom, etc.)
3. **Check** open source implementations (libnfc, nfc-tools, proxmark3, RFIDler)
4. **If found**: treat as Section A and document
5. **If not found as standard**: treat as Section C

---

## Section C — Scientific Experimentation Protocol

When X is undocumented, proprietary, or simply not findable online:

### 1. Formulate Hypothesis
```markdown
## Unknown Protocol Investigation: [X]

### What we observe
- [describe the bytes/behavior/API calls seen in the code]

### Initial hypothesis
- [what we think it might be and why]

### Unknowns
- [list of specific things we don't know]
```

### 2. Design Experiments

For each unknown, design a minimal experiment:

```markdown
### Experiment [N]: [What we're testing]

**Goal:** Determine [specific question]

**Method:**
1. [Exact steps to reproduce]
2. [What to send/trigger]
3. [What to observe/measure]

**Expected outcomes:**
- If hypothesis A is correct: [expected result A]
- If hypothesis B is correct: [expected result B]

**Required tools:** [list]
**Risk level:** [Low/Medium/High — will it damage hardware?]
```

### 3. Implement Experiment Code

Write a minimal test script in Python (preferred for portability):

```python
# experiment_[N]_[description].py
# Goal: [what this tests]
# Hypothesis: [what we think]
# Protocol: Unknown Domain Protocol v1.0
# Author: [agent name]
# Date: [date]

"""
EXPERIMENT [N]: [Title]
[Description of what this script tests and why]
"""

# [Minimal implementation]
# All findings are printed to stdout in structured format
```

Save to `experiments/[domain]/experiment_[N]_[description].py`

### 4. Document Findings

After experiments (real or simulated from code analysis), write findings:

```markdown
# [Protocol/Behavior Name] — Research Findings
## Status: UNDOCUMENTED / REVERSE-ENGINEERED / INFERRED

### Discovery Method
[How this was found: code analysis / hardware capture / experiment]

### Confidence Level
[High / Medium / Low — and why]

### Observed Behavior
[What the code/hardware actually does]

### Inferred Protocol
[Our best understanding of the protocol]

#### Frame Format
[ASCII table if applicable]

#### State Machine
[ASCII diagram if applicable]

#### Known Commands
| Command | Bytes | Purpose | Response |
|---------|-------|---------|----------|

#### Timing
[Any timing constraints observed]

### Open Questions
[What we still don't know]

### Experiments Run
[List of experiments and their results]

### References
[Any partial documentation found]

### Suggested Standard Equivalent
[If this looks like a variant of a known standard]
```

Save to `knowledge/[domain]/[name]-research.md`

---

## Cataloguing Requirements

Every new knowledge file (discovered or reverse-engineered) MUST include this header:

```markdown
---
type: [standard | datasheet | reverse-engineered | inferred]
source: [URL or "code analysis" or "hardware experimentation"]
confidence: [high | medium | low]
domain: [nfc | smartcard | usb | bluetooth | serial | embedded | unknown]
standard_body: [ISO | IETF | IEEE | NFC Forum | proprietary | unknown]
related_agents: [list of agents that use this knowledge]
date_added: [YYYY-MM-DD]
---
```

And MUST be indexed in `knowledge/INDEX.md`.

---

## Knowledge Index Template

`knowledge/INDEX.md` must stay up to date:

```markdown
# Knowledge Base Index

## NFC / RFID
- [ISO 15693](nfc/iso-15693.md) — Vicinity cards, standard, high confidence
- [ACR122U Commands](nfc/acr122u-commands.md) — Datasheet, high confidence
- [RF Field Timing](nfc/rf-field-timing.md) — Reverse-engineered + experimentation

## Smart Card / PC-SC
- [PC/SC API](smartcard/pcsc-api.md) — Standard (Microsoft/PCMCIA), high confidence
- [APDU Reference](smartcard/apdu-reference.md) — ISO 7816-4 standard, high confidence

## Reverse-Engineered / Research
[New entries go here when discovered]
```

---

## Consulting the Expert / Hardware Operator

The user IS the hardware operator. Agents MAY and SHOULD ask the user to:
- **Plug/unplug hardware** to observe behavior changes
- **Run a specific diagnostic** and report what they see on screen
- **Check a physical component** (LED state, USB connection, device manager)
- **Run the ASUS software** and observe what happens step by step
- **Answer domain questions** the agent cannot resolve from code alone

**Format for hardware requests:**
```
[HARDWARE ACTION NEEDED]
Please: <exact instruction>
Then report back: <what to observe and tell me>
Reason: <why this experiment matters>
```

Examples of valid hardware requests:
```
[HARDWARE ACTION NEEDED]
Please: Place the Keystone card on the reader, wait 3 seconds, then remove it.
Then report back: Does a sound play? Does anything change in Armoury Crate?
Reason: Confirming the baseline plug-in/plug-out cycle works.
```

```
[HARDWARE ACTION NEEDED]
Please: Unplug the NFC reader from USB (if external), wait 5 seconds, plug back in.
Then report back: Does it appear in Device Manager under "Smart card readers"?
Reason: Verifying the reader enumeration path.
```

**Format for expert questions:**
```
[EXPERT QUESTION]
I found X in the code but cannot determine Y.
Can you tell me: <specific question>
Context: <what was found>
```

---

## Experiments Are MANDATORY

When a hypothesis about hardware/software behavior is formed:
1. **Write the experiment script** — minimal Python code to test the hypothesis
2. **Run it** (or ask the user to run it if hardware interaction is needed)
3. **Document results** regardless of outcome
4. **Update the research file** with confirmed/rejected hypotheses

A hypothesis is NOT valid until it is tested.
An experiment that fails is as valuable as one that succeeds.

---

## Quick Reference for Agents

When stuck, run through this checklist:
```
[ ] Did I search for the standard name + "specification"?
[ ] Did I check manufacturer datasheet?
[ ] Did I check open source implementations?
[ ] Did I search GitHub for implementations?
[ ] Did I search nfc-tools, proxmark3, libnfc issues for mentions?
[ ] If still unknown: did I write experiment code?
[ ] Did I document findings even if incomplete?
[ ] Did I add entry to knowledge/INDEX.md?
```

**The worst outcome is silent ignorance. The correct outcome is documented uncertainty.**

## Collaboration & Learning Mandate

You are part of a unified, evolving agent team operating inside the Cornerstone
repository. You **MUST** follow these principles in every session:

1. **Share the Knowledge:** When you learn a domain quirk, solve a recurring
   issue, or find a reusable workaround, update the `learning-protocol` or your
   own `SKILL.md`. Knowledge hoarding is an anti-pattern.
2. **Domain Specialization:** Do not hallucinate skills outside your domain.
   If a task falls outside your expertise, delegate to the appropriate
   specialist agent — do not attempt it yourself.
3. **Use and Improve:** Before solving a problem, check whether another agent's
   `SKILL.md` already covers it. If an existing skill is flawed or incomplete,
   **refactor and improve that `SKILL.md`** rather than bypassing it.
4. **Just-In-Time Instantiation:** Be invoked exactly when your specific domain
   context is needed. Avoid accumulating massive monolithic contexts.

> Authority: `AGENTS.md § 1b — Collaborative Agentic Philosophy`.
> These rules apply to every agent, every session, no exceptions.
