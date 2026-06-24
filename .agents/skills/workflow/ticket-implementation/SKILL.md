---
name: ticket-implementation
description: "Expert implementation specialist using 7-hop multihop grounding process (DSPy-style). Executes atomic steps with iterative verification at each hop: Read, Validate Context, Pre-Execution Check, Execute, Verify, Ground, Validate Next."
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - mcp__ide__getDiagnostics
  - mcp__ide__executeCode
  - mcp__context7__resolve-library-id
  - mcp__context7__query-docs
  - mcp__atlassian__jira_get_issue
  - mcp__atlassian__jira_search
  - mcp__bitbucket__bb_get
  - mcp__squit-remote__squit_search
  - mcp__squit-remote__squit_dependencies
color: green
---

# Ticket Implementation Skill

## Core Expertise

### 7-Hop Multihop Grounding Process (DSPy-Style)

This is a **systematic, iterative execution process** where each step is validated through 7 hops before proceeding to the next step. This ensures quality, correctness, and alignment with the plan at every action.

#### Hop 1: Read
- **Action**: Read the current step from the plan
- **Goal**: Understand exactly what needs to be done
- **Validation**: Can I explain the step in my own words?
- **Output**: Clear understanding of the step requirements

#### Hop 2: Validate Context
- **Action**: Gather all necessary context for the step
- **Goal**: Ensure I have all information needed to execute correctly
- **Validation**: Do I have the relevant files, standards, and patterns?
- **Output**: Context checklist completed

**Context to validate:**
- Plan file (current step and surrounding context)
- Files to be modified or created
- Related files (dependencies, similar implementations)
- Standards documentation (applicable standards for the domain)
- Test patterns (existing tests for similar features)

#### Hop 3: Pre-Execution Check
- **Action**: Verify that execution is safe and appropriate
- **Goal**: Prevent unintended changes and ensure alignment
- **Validation**: Does the plan authorize this action? Is the context sufficient?
- **Output**: Pre-execution checklist passed

**Pre-execution checklist:**
- [ ] Plan explicitly describes this action
- [ ] I understand the file structure and syntax
- [ ] I have identified the correct location for changes
- [ ] I know the expected outcome
- [ ] I can verify success after execution

#### Hop 4: Execute
- **Action**: Perform the implementation action
- **Goal**: Make the code change described in the plan
- **Validation**: Action completed without errors
- **Output**: File(s) modified or created

**Execution guidelines:**
- Follow the plan exactly (unless you identify a critical issue)
- If you deviate from the plan, document why and update the plan
- Use atomic changes (one logical change per edit)
- Maintain code quality standards

#### Hop 5: Verify
- **Action**: Verify that the execution matches expectations
- **Goal**: Ensure correctness and completeness
- **Validation**: Does the result match the plan requirements?
- **Output**: Verification checklist passed

**Verification checklist:**
- [ ] Code was written/modified as intended
- [ ] No unintended changes were made
- [ ] Syntax is correct (no lint errors)
- [ ] Code follows project standards
- [ ] All dependencies are satisfied

#### Hop 6: Ground
- **Action**: Validate the result against the plan and standards
- **Goal**: Ensure alignment with requirements and quality standards
- **Validation**: Is the change consistent with the plan, standards, and patterns?
- **Output**: Grounding checklist passed

**Grounding checks:**
- [ ] Does the result match the plan requirements?
- [ ] Is the code consistent with project standards?
- [ ] Are all dependencies satisfied?
- [ ] Is the implementation complete for this step?
- [ ] Does it integrate correctly with existing code?

**If grounding fails:**
- Stop and analyze the issue
- Fix the problem
- Re-run Hop 5 (Verify) and Hop 6 (Ground)
- Only proceed when grounding passes

#### Hop 7: Validate Next
- **Action**: Validate that we're ready to proceed to the next step
- **Goal**: Ensure the workflow can continue smoothly
- **Validation**: Is the current step complete and verified?
- **Output**: Ready for next step (or loop back to fix issues)

**Validation checklist:**
- [ ] Current step is complete
- [ ] All quality checks passed
- [ ] No issues blocking the next step
- [ ] Plan is accurate (or updated if deviations occurred)

**If validation fails:**
- Identify the blocking issue
- Loop back to appropriate hop to fix
- Re-validate before proceeding

### Pre-Implementation Validation

Before starting the 7-hop process for the first step:

1. **Plan Existence Check**
   - Verify plan exists at expected path
   - Read and understand the complete plan
   - Identify all steps and their sequence

2. **Branch Verification**
   - Ensure we're on the correct feature branch
   - Verify branch name matches plan convention
   - If not on correct branch, ask user to switch

3. **Environment Validation**
   - Verify working directory is correct
   - Check that necessary tools are available
   - Verify dependencies are installed

### Atomic Execution

Each step in the plan must be executed as an **atomic action**:

**What makes an action atomic:**
- Single, focused change
- Can be understood independently
- Has clear success criteria
- Can be tested independently
- Doesn't leave the codebase in a broken state

**Examples of atomic actions:**
- Create one new file (function, component, module)
- Modify one specific function
- Add one test case
- Update one configuration setting
- Add one dependency

**Non-atomic (avoid):**
- Multiple unrelated changes in one action
- Changes that span multiple concerns
- Actions without clear boundaries

## When to Use

Invoke this skill when:
- User has an approved implementation plan
- User is ready to start implementing a feature
- User requests to "implement", "code", "build", or "develop" a planned feature
- Plan exists at `ai-specs/changes/[ticket-id]_backend.md` or `ai-specs/changes/[ticket-id]_frontend.md`

## Workflow

### Initial Setup

1. **Load Plan**
   - Read plan file from `ai-specs/changes/[ticket-id]_[backend|frontend].md`
   - Verify plan is complete and approved
   - Identify total number of steps

2. **Verify Branch**
   - Check current branch name
   - Verify it matches plan convention
   - If not correct: Notify user and wait

3. **Initialize Tracking**
   - Track current step number
   - Track completed steps
   - Track any issues or deviations

### Execution Loop (For Each Step)

For each step in the plan, execute the **7-Hop Multihop Grounding Process**:

```
READ → VALIDATE CONTEXT → PRE-EXECUTION CHECK → EXECUTE → VERIFY → GROUND → VALIDATE NEXT
```

**After completing all hops for a step:**
- Mark step as complete
- Log any deviations or issues
- Proceed to next step

**After completing all steps:**
- Run full test suite
- Verify all quality gates
- Stage files for commit
- Create commit with appropriate message
- Notify user of completion

### Error Handling

**If an error occurs during execution:**

1. **Identify the error**
   - What went wrong?
   - Which hop failed?
   - What is the error message?

2. **Analyze the cause**
   - Is it a plan issue (ambiguous, incorrect)?
   - Is it a context issue (missing information)?
   - Is it an execution issue (wrong approach)?

3. **Fix the issue**
   - If plan issue: Update plan with clarification
   - If context issue: Gather missing context
   - If execution issue: Correct the implementation

4. **Re-validate**
   - Re-run appropriate hops
   - Ensure grounding passes
   - Only then proceed

### Quality Gates

**After completing all steps, verify:**

1. **Code Quality**
   - All linting checks pass
   - Type checking passes (if applicable)
   - Code follows project standards

2. **Functionality**
   - All tests pass (unit, integration, e2e)
   - Manual testing if required
   - Acceptance criteria met

3. **Documentation**
   - Technical documentation updated
   - API documentation updated (if applicable)
   - README updated (if applicable)

4. **Integration**
   - No breaking changes to existing functionality
   - Dependencies are correct
   - Configuration is correct

**If any quality gate fails:**
- Identify the failing items
- Fix the issues
- Re-run quality gates
- Only proceed when all gates pass

## Output

### During Execution

**After each step:**
- Confirm step completion
- Report any deviations or issues
- Show progress (e.g., "Step 3 of 10 complete")

**On error:**
- Report the error clearly
- Explain what went wrong
- Propose a fix or ask for guidance

### After Completion

**Success report includes:**
- Total steps completed
- Any deviations from the plan
- Test results summary
- Quality gates status
- Commit details
- Next steps (e.g., create PR)

**Example success message:**
```
✅ Implementation complete for [TICKET-ID]

Steps completed: 12/12
Tests: 47 passed, 0 failed
Coverage: 92%
Quality gates: All passed

Branch: feature/[ticket-id]-backend
Commit: abc1234 - Implement [feature description]

Next steps:
1. Review changes: git diff
2. Run tests: make test
3. Create PR: (instructions)
```

## Notes

- The 7-hop process is **mandatory** for each step - no shortcuts
- Grounding failures must be **fixed immediately** - never proceed with known issues
- All deviations from the plan must be **documented** in the plan file
- Quality gates are **blocking** - implementation is not complete until all pass
- The skill maintains **context** across all steps
- Each hop produces a **verifiable output** before proceeding to the next
- The process is **iterative** - loop back as needed to fix issues
- The process ensures **traceability** - every action can be traced to a plan step
