export interface Project {
  id: string
  name: string
  slug: string
  description?: string
  jira_project_key?: string
  splunk_index?: string
  ocp_namespace?: string
  jenkins_job_pattern?: string
  component_owner_map?: Record<string, unknown>
  start_date?: string | null
  end_date?: string | null
  tags?: string[]
  is_active: boolean
  created_at: string
  updated_at?: string
}

export interface ProjectUpdate {
  name?: string
  description?: string | null
  jira_project_key?: string | null
  splunk_index?: string | null
  ocp_namespace?: string | null
  jenkins_job_pattern?: string | null
  start_date?: string | null
  end_date?: string | null
  tags?: string[]
}
