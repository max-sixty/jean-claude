---
name: reviewing-cc-logs
description: "Reviews Claude Code conversation logs to analyze behavior gaps. Use when examining why Claude did something unexpected, reviewing a conversation for improvement opportunities, or debugging skill/code issues."
---

# Reviewing Claude Code Logs

This skill guides systematic analysis of Claude Code conversations to identify
gaps between expected and actual behavior, and determine whether fixes belong
in code, skill, or both.

## Finding the Conversation

Claude Code stores conversations in `~/.claude/projects/`. Directory names
correspond to working directories with path separators replaced by dashes.

```bash
# Find the project directory for a working directory
ls -la ~/.claude/projects/ | grep -i "project-name"

# List conversations in that project (most recent first by modification time)
ls -lt ~/.claude/projects/<PROJECT_DIR>/*.jsonl | head -10

# Search for specific content across conversations
grep -l "search term" ~/.claude/projects/<PROJECT_DIR>/*.jsonl
```

**JSONL structure:**
- Each line has `type`: `user`, `assistant`, or `file-history-snapshot`
- User/assistant lines have `message` containing `role` and `content`
- Metadata: `uuid`, `parentUuid` (for message chains), `timestamp`, `cwd`, `gitBranch`
- **Agent subconversations** are in separate `agent-*.jsonl` files — check these
  when debugging issues that involved subagent tool calls

## Extracting the Conversation

**Quick tool inspection** (shows tool calls only):

```bash
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use") |
  "\(.name): \(.input.command // .input | tostring | .[0:100])"' <FILE>.jsonl
```

**Full message extraction:**

```bash
jq -r 'select(.type == "user" or .type == "assistant") | .message |
  "\n--- \(.role) ---\n" + (
    if .content | type == "array" then
      .content | map(
        if .type == "tool_use" then "TOOL[\(.name)]: \(.input | tojson | .[0:500])"
        elif .type == "tool_result" then "RESULT: \(.content | tostring | .[0:500])"
        elif .type == "text" then .text
        else empty end
      ) | join("\n")
    elif .content | type == "string" then .content
    else .content | tostring end
  )' <FILE>.jsonl
```

## Analysis Framework

### 1. Identify the Gap

Document precisely:

- **Expected behavior:** What the user wanted to happen
- **Actual behavior:** What Claude actually did
- **Gap:** The specific difference

Be concrete. "It didn't work" is not a gap. "Claude used `draft create` with
manual `--to` and `--subject` flags instead of `draft reply` with MESSAGE_ID,
causing the email to start a new thread rather than reply in the existing one"
is a gap.

### 2. Find the Actual Behavior

Don't rely on extracts — find the actual tool calls in the JSONL:

```bash
# Find exactly what bash commands were run
jq -r 'select(.type == "assistant") | .message.content[]? |
  select(.type == "tool_use" and .name == "Bash") |
  .input.command' <FILE>.jsonl
```

Look for:
- The exact command executed
- The arguments passed
- The output received
- What decision Claude made based on the output

### 3. Trace the Decision Path

Understand why Claude made the choice it did:

1. **What did Claude know?** What context was available at decision time?
2. **What did the skill say?** Check the skill documentation Claude received
3. **What was ambiguous?** Where could Claude have gone either way?
4. **What was the skill missing?** What guidance would have prevented the issue?

### 4. Categorize the Issue

**Skill gap:** The skill doesn't mention the distinction or gives inadequate
guidance. Fix: update SKILL.md.

**Code gap:** The CLI behavior doesn't match what agents need. Fix: update code.

**Both:** The code works but the skill doesn't guide agents to use it correctly.
Fix: both.

**Neither (user error):** The user asked for something that wasn't what they
wanted. Document as a known edge case if it's likely to recur.

## Common Patterns

### Wrong Command Used

Claude picked a more general/flexible command instead of the specific one for
the task.

Check:
- Does the skill clearly distinguish when to use each command?
- Is the recommended command shown first or prominently?
- Are the consequences of using the wrong command visible?

### Missing Functionality

The output doesn't match what the user expected (missing data, wrong format,
incomplete results).

Check:
- Does the code handle this case?
- Is there a flag or option missing?
- Is the API being used correctly?

### Misinterpreted Context

Claude did extra work (searching, looking up) instead of using data already
available in the conversation.

Check:
- Did the skill guide Claude to reuse existing data?
- Was the connection between earlier context and the current task clear?
- Should the skill be more explicit about data flow?

## Improvement Recommendations

### Skill Improvements

When recommending skill changes:

1. **Quote the current text** that led to the issue
2. **Propose specific replacement text**
3. **Explain why the change prevents the issue**

Example:

**Current:** The skill shows `draft create` examples first, then `draft reply`.

**Proposed:** Add explicit guidance: "When responding to an existing email,
ALWAYS use `draft reply MESSAGE_ID` to preserve threading. Use `draft create`
only for new conversations."

**Why:** Claude defaulted to `draft create` because it appeared first and seemed
more flexible.

### Code Improvements

When recommending code changes:

1. **Describe the current behavior**
2. **Describe the desired behavior**
3. **Note that skill should still encapsulate complexity** — the code serves
   the skill, not vice versa

Example:

**Current:** The `--to` flag requires looking up recipient email addresses
separately.

**Desired:** The `reply` command extracts the correct reply-to address
automatically from the original message.

**Note:** The skill should guide agents to use the simpler command; the code
should encapsulate the complexity.

## Report Template

```markdown
## Issue Summary

[One sentence: what went wrong]

## Gap Analysis

**Expected:** [What user wanted]
**Actual:** [What happened]
**Gap:** [Specific difference]

## Root Cause

[Why Claude made this choice — trace the decision path]

## Evidence

[Exact tool calls from the log]

## Recommendation

### Skill Changes

[Specific text changes to SKILL.md, or "none needed"]

### Code Changes

[Specific behavior changes, or "none needed"]

## Prevention

[How to prevent similar issues — skill guidance, code guardrails, etc.]
```
