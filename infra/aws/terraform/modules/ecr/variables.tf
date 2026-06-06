variable "repositories" {
  description = "List of repository names (e.g. testlookup/backend)."
  type        = list(string)
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}
