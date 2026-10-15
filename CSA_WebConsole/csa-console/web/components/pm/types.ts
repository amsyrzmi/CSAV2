export type TaskPriority = "low" | "medium" | "high" | "urgent";
export type TaskStatus = "backlog" | "ready" | "in_progress" | "review" | "done" | "cancelled";

export type AcceptanceCriterion = {
  id: string;
  text: string;
  done: boolean;
};

export type TaskDependencyRef = {
  dep_id?: string;
  task_id: string;
  title: string;
  status: TaskStatus;
  priority?: TaskPriority;
};

export type Milestone = {
  id: string;
  project_id: string;
  title: string;
  description: string;
  due_at: string | null;
  status: "open" | "completed" | "cancelled";
  acceptance_criteria: AcceptanceCriterion[];
  position: number;
  version: number;
  total_tasks?: number;
  completed_tasks?: number;
  created_at: string;
  updated_at: string;
};

export type Task = {
  id: string;
  project_id: string;
  milestone_id: string | null;
  milestone_title?: string | null;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  human_assignee_id: string | null;
  assignee_name?: string | null;
  assignee_email?: string | null;
  agent_assignment_id?: string | null;
  acceptance_criteria: AcceptanceCriterion[];
  labels: string[];
  position: number;
  version: number;
  override_reason?: string | null;
  cancellation_reason?: string | null;
  is_blocked: boolean;
  blocked_by: TaskDependencyRef[];
  dependency_count: number;
  comment_count: number;
  created_at: string;
  updated_at: string;
};

export type TaskLinkedNote = {
  note_id: string;
  note_title: string;
  note_folder: string;
  link_type: "reference" | "evidence" | "spec";
  note_revision_id: string | null;
  revision_number?: number | null;
  created_at?: string;
};

export type TaskDetail = Task & {
  predecessors: TaskDependencyRef[];
  successors: TaskDependencyRef[];
  linked_notes?: TaskLinkedNote[];
};

export type Assignee = {
  id: string;
  email: string;
  display_name: string;
  role: string;
};

export type TaskComment = {
  id: string;
  task_id: string;
  author_user_id: string | null;
  author_name: string | null;
  author_email: string | null;
  author_assignment_id?: string | null;
  body: string;
  visibility: "admin_only" | "client_visible";
  created_at: string;
  updated_at: string;
};

export type ActivityItem = {
  id: number;
  at: string;
  actor_user_id: string | null;
  actor_name: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  summary: string;
  details: Record<string, unknown>;
};

export type Note = {
  id: string;
  project_id: string;
  title: string;
  folder: string;
  tags: string[];
  current_revision_id: string | null;
  revision_number: number;
  visibility: "admin_only" | "client_visible";
  version: number;
  backlink_count: number;
  linked_task_count: number;
  created_by: string | null;
  author_name: string | null;
  author_email: string | null;
  created_at: string;
  updated_at: string;
};

export type NoteRevision = {
  id: string;
  revision_number: number;
  title: string;
  content_hash: string;
  author_name: string | null;
  author_email: string | null;
  created_at: string;
};

export type NoteBacklink = {
  source_note_id: string;
  source_title: string;
  source_folder: string;
  link_text: string;
};

export type NoteForwardLink = {
  target_note_id: string;
  target_title: string;
  target_folder: string;
  link_text: string;
};

export type NoteDetail = Note & {
  body_markdown: string;
  content_hash: string;
  backlinks: NoteBacklink[];
  outgoing_links: NoteForwardLink[];
  linked_tasks: {
    task_id: string;
    task_title: string;
    task_status: string;
    task_priority: string;
    link_type: string;
    note_revision_id: string | null;
  }[];
  revisions: NoteRevision[];
};

export type FolderTreeNode = {
  path: string;
  name: string;
  notes: {
    id: string;
    title: string;
    folder: string;
    tags: string[];
    updated_at: string;
  }[];
  folders: FolderTreeNode[];
  total_notes: number;
};

export type NoteSearchResult = {
  id: string;
  title: string;
  folder: string;
  tags: string[];
  snippet: string;
  rank: number;
  updated_at: string;
};

export type NoteGraphNode = {
  id: string;
  title: string;
  folder: string;
  tags: string[];
  is_selected?: boolean;
};

export type NoteGraphLink = {
  source: string;
  target: string;
  label: string;
};

export type NoteGraphData = {
  nodes: NoteGraphNode[];
  links: NoteGraphLink[];
};

export type StarterTemplate = {
  id: string;
  name: string;
  description: string;
  folder: string;
  tags: string[];
  body: string;
};

// ==============================================================================
// Phase 04: Planner, Proposals & Runs Types
// ==============================================================================

export type PlanTaskDraft = {
  id: string;
  milestone_id?: string | null;
  title: string;
  description: string;
  priority: TaskPriority;
  estimated_effort?: string;
  acceptance_criteria: { item: string; completed: boolean }[];
  depends_on: string[];
};

export type PlanMilestoneDraft = {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
};

export type StructuredPlan = {
  goals: string[];
  assumptions: string[];
  risks: { risk: string; mitigation: string }[];
  milestones: PlanMilestoneDraft[];
  tasks: PlanTaskDraft[];
  source_revision_refs: { note_id?: string; revision_id?: string; title: string }[];
};

export type ProposalDetail = {
  id: string;
  project_id: string;
  run_id: string;
  revision_number: number;
  proposal_hash: string;
  structured_body: StructuredPlan;
  acceptance_map: Record<string, unknown>;
  status: "draft" | "accepted" | "rejected" | "superseded";
  created_at: string;
  updated_at: string;
};

export type RunDetail = {
  id: string;
  project_id: string;
  mode: "plan" | "investigate" | "review";
  status: "queued" | "running" | "waiting_approval" | "waiting_review" | "waiting_input" | "cancelling" | "cancelled" | "succeeded" | "failed";
  thread_id: string;
  budgets: Record<string, unknown>;
  usage: Record<string, unknown>;
  error_message?: string | null;
  latest_proposal_id?: string | null;
  events_url: string;
  created_at: string;
  updated_at: string;
};

export type RunEvent = {
  id: string;
  project_id: string;
  run_id: string;
  sequence: number;
  event_type: string;
  public_payload: Record<string, unknown>;
  created_at: string;
};

// ==============================================================================
// Phase 05: Diagram Studio & Architecture Model Types
// ==============================================================================

export type DiagramFormat = "mermaid" | "plantuml" | "architecture_json";
export type ArtifactKind = "diagram" | "architecture" | "adr";
export type ValidationStatus = "valid" | "invalid" | "unsupported";

export type ArchitectureNode = {
  id: string;
  label: string;
  category: "compute" | "database" | "storage" | "network" | "security" | "queue" | "analytics" | "custom";
  service_type: string;
  boundary_id?: string | null;
  description?: string | null;
  properties?: Record<string, unknown>;
};

export type ArchitectureBoundary = {
  id: string;
  label: string;
  kind: "account" | "region" | "vpc" | "az" | "subnet" | "trust_zone" | "custom";
  parent_boundary_id?: string | null;
  cidr?: string | null;
  is_public?: boolean;
};

export type ArchitectureEdge = {
  id: string;
  source: string;
  target: string;
  label?: string | null;
  protocol?: string | null;
  data_flow?: "read" | "write" | "bidirectional";
  crosses_trust_boundary?: boolean;
  description?: string | null;
};

export type ArchitectureGraph = {
  nodes: ArchitectureNode[];
  boundaries: ArchitectureBoundary[];
  edges: ArchitectureEdge[];
};

export type ArtifactRevisionDetail = {
  id: string;
  artifact_id: string;
  project_id: string;
  revision_number: number;
  format: DiagramFormat;
  diagram_type: string;
  source_text?: string | null;
  source_json?: ArchitectureGraph | null;
  layout_json?: Record<string, { x: number; y: number; width?: number; height?: number }>;
  assumptions: string[];
  evidence: string[];
  trust_boundaries: string[];
  failure_modes: string[];
  adr_content?: string | null;
  validation_status: ValidationStatus;
  validation_errors: string[];
  content_hash: string;
  generation_run_id?: string | null;
  created_by?: string | null;
  created_at: string;
};

export type ArtifactDetail = {
  id: string;
  project_id: string;
  kind: ArtifactKind;
  title: string;
  description?: string | null;
  current_revision_id?: string | null;
  current_revision?: ArtifactRevisionDetail | null;
  revisions: ArtifactRevisionDetail[];
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type CatalogItem = {
  id: string;
  engine: DiagramFormat;
  diagram_type: string;
  label: string;
  category: string;
  description: string;
  enabled: boolean;
  example_fixture: string;
  keywords: string[];
};

export type ProjectRepository = {
  id: string;
  project_id: string;
  approved_remote: string;
  default_branch: string;
  credential_ref?: string | null;
  last_indexed_commit?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type ProjectWorkspace = {
  id: string;
  project_id: string;
  repository_id?: string | null;
  base_commit?: string | null;
  runner_id: string;
  sandbox_locator: string;
  workspace_type: "human_terminal" | "agent_run";
  status: "provisioning" | "ready" | "failed" | "archived";
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type TerminalSession = {
  id: string;
  project_id: string;
  workspace_id: string;
  owner_user_id: string;
  runner_pty_id?: string | null;
  title: string;
  state: "active" | "detached" | "terminated" | "expired";
  created_at: string;
  last_attached_at: string;
  expires_at: string;
  terminated_at?: string | null;
};

export type RunnerStatus = {
  runner_id: string;
  status: "ready" | "degraded" | "offline";
  runner_type: string;
  workspaces_dir: string;
  active_ptys: number;
  max_scrollback_bytes: number;
};

export type EvidenceLocation = {
  file_path: string;
  line_start?: number | null;
  line_end?: number | null;
  commit_hash?: string | null;
  snippet?: string | null;
};

export type BugInvestigation = {
  id: string;
  project_id: string;
  task_id?: string | null;
  workspace_id?: string | null;
  external_job_id?: string | null;
  title: string;
  status: "queued" | "running" | "reproduced" | "suspected" | "inconclusive" | "failed" | "cancelled";
  symptom: string;
  reproduction_command?: string | null;
  observed_result?: string | null;
  expected_result?: string | null;
  confidence: "low" | "medium" | "high";
  evidence_locations: EvidenceLocation[];
  suspected_causes: string[];
  proposed_fix?: string | null;
  patch_artifact_id?: string | null;
  missing_info: string[];
  telemetry: Record<string, unknown>;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type HermesCapabilities = {
  runtime_version: string;
  installed: boolean;
  provider: string;
  supported_modes: string[];
  features: string[];
  isolated_homes_dir: string;
};

// ==========================================
// Phase 08: Multi-Agent Workroom Types
// ==========================================

export type WorkroomRole =
  | "planner"
  | "architect"
  | "investigator"
  | "implementer"
  | "reviewer"
  | "qa"
  | "human_operator";

export type WorkroomMessageType =
  | "proposal"
  | "question"
  | "evidence"
  | "handoff"
  | "review"
  | "final";

export type WorkroomRunStage =
  | "queued"
  | "investigating"
  | "implementing"
  | "reviewing"
  | "repairing"
  | "qa_testing"
  | "review_ready"
  | "approved"
  | "rejected"
  | "conflict_detected"
  | "budget_exceeded"
  | "cancelled";

export type TaskCompletionStatus =
  | "pending"
  | "patch_proposed"
  | "tests_passed_sandbox"
  | "published_repository"
  | "deployed";

export type WorkroomAgent = {
  definition_id: string;
  name: string;
  role: string;
  active: boolean;
  current_version_id?: string | null;
  current_version_number?: number | null;
  soul_text?: string | null;
  capabilities: string[];
  model_policy_ref?: string | null;
  is_assigned: boolean;
  assignment_id?: string | null;
  pinned_version_id?: string | null;
  pinned_version_number?: number | null;
  enabled: boolean;
  policy_override: Record<string, unknown>;
};

export type WorkroomMessage = {
  id: string;
  project_id: string;
  run_id: string;
  task_id?: string | null;
  author_role: WorkroomRole;
  author_assignment_id?: string | null;
  message_type: WorkroomMessageType;
  summary: string;
  body_markdown: string;
  evidence_refs: Array<Record<string, unknown>>;
  target_role?: WorkroomRole | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type WorkroomRun = {
  id: string;
  project_id: string;
  task_id?: string | null;
  workspace_id?: string | null;
  title: string;
  workflow_type: string;
  status: string;
  current_stage: WorkroomRunStage;
  task_completion_status: TaskCompletionStatus;
  active_role: WorkroomRole;
  repair_cycles: number;
  max_repair_cycles: number;
  budgets: Record<string, unknown>;
  usage: {
    tokens_used?: number;
    wall_time_sec?: number;
    estimated_cost_usd?: number;
    subagent_calls?: number;
  };
  review_bundle: Record<string, unknown>;
  patch_artifact_id?: string | null;
  conflict_artifact_id?: string | null;
  error_message?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkroomBudgetStatus = {
  active_runs: number;
  max_concurrent_runs: number;
  total_tokens_used: number;
  total_wall_time_sec: number;
  total_cost_usd: number;
  total_subagent_calls: number;
  limits: {
    max_tokens_per_run: number;
    max_wall_time_sec: number;
    max_cost_usd: number;
    max_repair_cycles: number;
  };
};

export type ConflictCheckResult = {
  has_conflict: boolean;
  conflict_files: string[];
  conflict_artifact_id?: string | null;
  summary: string;
  conflict_details: Array<Record<string, unknown>>;
};



