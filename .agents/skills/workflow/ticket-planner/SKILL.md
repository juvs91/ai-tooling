---
name: ticket-planner
description: Expert planning analyst for breaking down Jira tickets into detailed implementation plans. Uses pre-planning bloat methodology with 11-source context gathering, iterative grokking refinement, and template-based plan generation for both backend (DDD layered architecture) and frontend (component-driven architecture) domains.
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - mcp__atlassian__jira_get_issue
  - mcp__atlassian__confluence_get_page
  - mcp__atlassian__jira_search
  - mcp__atlassian__confluence_search
  - mcp__bitbucket__bb_get
  - mcp__squit-remote__squit_search
  - mcp__squit-remote__squit_dependencies
  - mcp__squit-remote__squit_impact
  - mcp__squit-remote__squit_get_code
  - mcp__memory__create_entities
  - mcp__memory__create_relations
  - mcp__memory__add_observations
  - mcp__memory__search_nodes
  - mcp__memory__open_nodes
color: blue
---

# Ticket Planner Skill

## Core Expertise

### Pre-Planning Bloat Methodology (11-Source Context Gathering)

**This is a MANDATORY blocking process** - planning cannot proceed until all context checks pass.

#### Context Check 1: Ticket Information (AUTOMATIC)

**Agent MUST automatically:**
- Get ticket details via MCP Jira tool using ticket ID
- Read ticket description, acceptance criteria, comments completely
- Understand what the ticket is asking for
- Identify any ticket dependencies or related tickets

**If MCP unavailable:** Ask user for ticket details
**If local file mentioned:** Read the local file instead

#### Context Check 2: Codebase Context (AUTOMATIC)

**Agent MUST automatically:**
- Search codebase for files/components mentioned in ticket
- Read all files that will be affected or are related
- Review existing similar features to understand patterns
- Map dependencies and relationships between components
- Review existing test patterns for similar features

#### Context Check 3: Standards Context (CONDITIONAL)

**Apply based on request type:**
- **Base standards**: Always read `ai-specs/specs/base-standards.mdc`
- **Backend standards**: If backend work → read `ai-specs/specs/backend-standards.mdc`
- **Frontend standards**: If frontend work → read `ai-specs/specs/frontend-standards.mdc`
- **API spec**: If API integration → read `ai-specs/specs/api-spec.yml`
- **Data model**: If data structure changes → read `ai-specs/specs/data-model.md`
- **Documentation standards**: Always read `ai-specs/specs/documentation-standards.mdc`

#### Context Check 4: User Context Validation (ASK USER if insufficient)

**Context sufficiency check:**
- Do I have enough technical detail to create a complete plan?
- Are there any ambiguities or missing information?

**If context insufficient:**
- STOP planning immediately
- Ask user SPECIFIC questions (not generic)
- Wait for user response
- Re-validate context sufficiency

### Grokking Refinement Process (MANDATORY BEFORE PLANNING)

**This process ensures deep understanding before creating the plan.**

#### Step 1: Deep Understanding Check
- Can I explain the problem in my own words?
- Do I understand WHY this feature is needed?
- Do I understand HOW this should be implemented?
- Is the scope of work clear and bounded?

#### Step 2: Gap Identification
- Are there any ambiguous requirements?
- Are there missing technical details?
- Are any requirements unclear or conflicting?
- Are edge cases and error scenarios defined?

#### Step 3: User Questions (If gaps found)
**If ANY gap identified:**
- STOP planning
- Ask user SPECIFIC questions
- Wait for user response
- Re-validate understanding

#### Step 4: Validation Loop (ITERATIVE)
- Re-read ticket with new context
- Validate understanding is complete
- If still gaps: Repeat Step 3
- If complete: Proceed to planning

### Plan Generation

After all checks pass and grokking is achieved, generate a granular step-by-step plan following the appropriate template:

**Backend Template Structure:**
1. Header (Title with TICKET-ID)
2. Overview (Brief description + DDD architecture principles)
3. Architecture Context (Layers, files, dependencies)
4. Implementation Steps (Atomic, numbered steps starting with branch creation)
5. Implementation Order
6. Testing Checklist
7. Error Handling Patterns
8. Dependencies
9. Notes
10. Next Steps
11. Implementation Verification

**Frontend Template Structure:**
1. Header (Title with TICKET-ID)
2. Overview (Brief description + component-driven architecture)
3. Architecture Context (Components, services, routing, state management)
4. Implementation Steps (Atomic steps including branch creation, service layer, components, tests, documentation)
5. Implementation Order
6. Testing Checklist
7. Error Handling Patterns
8. UI/UX Considerations
9. Dependencies
10. Notes
11. Next Steps
12. Implementation Verification

## When to Use

Invoke this skill when:
- User needs to create an implementation plan for a Jira ticket
- Starting work on a new feature or bug fix
- Requirement calls for "breaking down" or "planning" a ticket
- User provides a ticket ID and asks "how should I implement this?"

## Workflow

### Phase 1: Context Gathering (BLOCKING)

1. **Load Ticket Context**
   - Fetch ticket via MCP or read local file
   - Extract all requirements, acceptance criteria, comments
   - Identify all mentioned components/files/endpoints

2. **Load Codebase Context**
   - Search for related files mentioned in ticket
   - Read existing implementations of similar features
   - Understand current architecture patterns
   - Map dependencies and relationships
   - Review existing test patterns

3. **Load Standards Context**
   - Read applicable standards based on ticket type
   - Understand testing requirements
   - Understand documentation requirements
   - Understand code quality requirements

4. **Validate Context Sufficiency**
   - Can I create a complete plan with this context?
   - If NO: Ask user for missing information (specific questions)
   - If YES: Proceed to Phase 2

### Phase 2: Grokking (ITERATIVE)

1. **Deep Understanding Check**
   - Explain the problem in your own words
   - Identify business context
   - Identify technical approach
   - Verify scope clarity

2. **Gap Identification**
   - List ambiguities
   - List missing details
   - List unclear requirements
   - List undefined edge cases

3. **User Questions (If gaps found)**
   - STOP planning
   - Ask specific questions
   - Wait for response
   - Re-validate understanding

4. **Validation Loop**
   - Re-read ticket with new context
   - Validate understanding is complete
   - Repeat if gaps remain
   - Proceed to Phase 3 when complete

### Phase 3: Plan Generation

1. **Determine Domain**
   - Backend: Use backend template (DDD layered architecture)
   - Frontend: Use frontend template (component-driven architecture)

2. **Generate Atomic Steps**
   - Each step must be a single, atomic action
   - Steps must be clearly defined and executable
   - Each step must have clear success criteria
   - Steps must be testable independently

3. **Apply Domain-Specific Guidance**

   **Backend (DDD Layered Architecture):**
   - Domain layer: Entities, value objects, domain services
   - Application layer: Use cases, application services
   - Infrastructure layer: Repository implementations, external services
   - Presentation layer: API endpoints, controllers

   **Frontend (Component-Driven Architecture):**
   - Component layer: React components, hooks
   - Service layer: API communication, data transformation
   - Routing layer: Route configuration, navigation
   - State management: Local state, context, or state library

4. **Write Plan to File**
   - Backend: `ai-specs/changes/[ticket-id]_backend.md`
   - Frontend: `ai-specs/changes/[ticket-id]_frontend.md`

5. **Notify User**
   - Inform user that plan is ready
   - Connect to implementation workflow
   - Remind user of next steps

## Output Format

### For Backend Tickets

Markdown document at `ai-specs/changes/[jira_id]_backend.md` containing:

1. **Header**: `# Backend Implementation Plan: [TICKET-ID] [Feature Name]`

2. **Overview**: Brief description with DDD architecture principles

3. **Architecture Context**:
   - Layers involved (Domain, Application, Infrastructure, Presentation)
   - Files referenced
   - Dependencies and relationships

4. **Implementation Steps** (starting with Step 0: Branch Creation):
   - Step 0: Create feature branch
   - Step 1: Domain layer changes
   - Step 2: Application layer changes
   - Step 3: Infrastructure layer changes
   - Step 4: Presentation layer changes
   - Step 5: Tests (unit, integration)
   - Step 6: Documentation updates

5. **Implementation Order**: Numbered sequence

6. **Testing Checklist**: Post-implementation verification

7. **Error Handling Patterns**: Exception handling, validation

8. **Dependencies**: External libraries, services

9. **Notes**: Important reminders, business rules, language requirements

10. **Next Steps**: Post-implementation tasks

11. **Implementation Verification**: Final checklist

### For Frontend Tickets

Markdown document at `ai-specs/changes/[jira_id]_frontend.md` containing:

1. **Header**: `# Frontend Implementation Plan: [TICKET-ID] [Feature Name]`

2. **Overview**: Brief description with component-driven architecture principles

3. **Architecture Context**:
   - Components/services involved
   - Files referenced
   - Routing considerations
   - State management approach

4. **Implementation Steps** (starting with Step 0: Branch Creation):
   - Step 0: Create feature branch (`feature/[ticket-id]-frontend`)
   - Step 1: Update/Create Service Methods
   - Step 2: Create/Update Components
   - Step 3: Update Routing
   - Step 4: Write Cypress E2E Tests
   - Step 5: Update Technical Documentation

5. **Implementation Order**: Numbered sequence

6. **Testing Checklist**: Post-implementation verification

7. **Error Handling Patterns**: Error states, user-friendly messages

8. **UI/UX Considerations**: Bootstrap components, responsive design, accessibility

9. **Dependencies**: External libraries, React Bootstrap components

10. **Notes**: Important reminders, language requirements (English only)

11. **Next Steps**: Post-implementation tasks

12. **Implementation Verification**: Final checklist

## Domain-Specific Guidance

### Backend DDD Layered Architecture

**Domain Layer** (Business logic, no external dependencies):
- Entities: Core business objects with identity
- Value Objects: Immutable values without identity
- Domain Services: Business logic that doesn't fit in entities
- Domain Events: Something that happened in the domain

**Application Layer** (Orchestration, use cases):
- Use Cases: Application-specific business workflows
- Application Services: Orchestrate domain objects
- DTOs: Data transfer objects for external communication

**Infrastructure Layer** (External concerns):
- Repository Implementations: Database access
- External Services: Third-party integrations
- Mappers: Convert between layers

**Presentation Layer** (API):
- Controllers/Endpoints: HTTP handlers
- Request/Response Models: API contracts
- Routing: URL configuration

### Frontend Component-Driven Architecture

**Component Layer** (UI building blocks):
- Presentational Components: Pure UI, receive props, emit events
- Container Components: Connect to services, manage state
- Custom Hooks: Reusable stateful logic
- Context: Shared state across components

**Service Layer** (API communication):
- API Services: HTTP client methods (GET, POST, PUT, DELETE)
- Data Transformation: Convert API responses to component models
- Error Handling: Standardized error responses

**Routing Layer**:
- Route Configuration: URL to component mapping
- Navigation Guards: Route protection and redirects
- Lazy Loading: Code splitting for performance

**State Management**:
- Local State: Component-level (useState)
- Context API: Cross-component state
- Server State: API data (React Query, SWR)
- URL State: Query params, route params

## Notes

- This skill enforces the **pre-planning bloat methodology** - planning is blocked without complete context
- The **grokking process** ensures deep understanding through iterative gap analysis
- All plans must be **granular** with atomic steps
- Plans must follow the **template structure** for consistency
- The skill automatically detects backend vs frontend based on ticket content
- Branch naming is enforced: `feature/[ticket-id]-frontend` for frontend work to separate concerns
- Documentation updates are **MANDATORY** before implementation is considered complete
