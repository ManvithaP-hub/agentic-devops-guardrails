package devops.guardrail

# ── Allow rules ───────────────────────────────────────────────────────────────

# Allow low-risk operations unconditionally (blast < 0.3)
allow {
    input.blast_radius < 0.3
}

# Allow medium-risk in confirmed non-production environments
allow {
    input.blast_radius >= 0.3
    input.blast_radius < 0.9
    is_staging(input.prompt)
    not is_production(input.prompt)
}

# Allow medium-risk with valid HITL approval token
allow {
    input.blast_radius >= 0.3
    input.blast_radius < 0.9
    input.hitl_approved == true
}

# ── Deny rules ────────────────────────────────────────────────────────────────

# Deny production mutations (medium-risk targeting production)
deny[reason] {
    input.blast_radius >= 0.3
    input.blast_radius < 0.9
    is_production(input.prompt)
    not input.hitl_approved
    reason := "production mutation denied — requires HITL approval"
}

# Deny high-blast without HITL approval (silence equals rejection)
deny[reason] {
    input.blast_radius >= 0.9
    not input.hitl_approved
    reason := "high-blast action requires HITL approval — silence equals rejection"
}

# Deny :latest image tags (unversioned images in any environment)
deny[reason] {
    contains(input.prompt, ":latest")
    reason := "unversioned :latest image tag denied"
}

# Deny kube-system namespace mutations unconditionally
deny[reason] {
    contains(input.prompt, "kube-system")
    reason := "kube-system namespace mutation denied unconditionally"
}

# ── Helper functions ──────────────────────────────────────────────────────────

# Detect production environment context
is_production(prompt) {
    production_terms := ["production", " prod ", "prod-", "-prod", "prd-", "-prd"]
    term := production_terms[_]
    contains(lower(prompt), term)
}

# Detect staging/non-production environment context
is_staging(prompt) {
    staging_terms := ["staging", " stage ", "stg-", "-stg", " dev ",
                      "sandbox", "-test", "test-", "nonprod", "non-prod"]
    term := staging_terms[_]
    contains(lower(prompt), term)
}
