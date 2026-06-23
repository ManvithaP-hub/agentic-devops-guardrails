package devops.guardrail

import future.keywords.if
import future.keywords.in

default allow = false
default deny_reason = "default deny"

# ── ALLOW: all low-risk read-only ops — unconditional ──────────
allow if { input.risk_level == "low" }

# ── ALLOW: medium risk in staging/dev ──────────────────────────
allow if {
    input.risk_level == "medium"
    not is_production
}

# ── ALLOW: medium with HITL approval ───────────────────────────
allow if {
    input.risk_level == "medium"
    input.hitl_approved == true
}

# ── DENY: production mutations ─────────────────────────────────
deny_reason = "DENY: production — Change Request required" if {
    is_production
    input.risk_level in ["medium","high"]
    not input.hitl_approved
}

# ── DENY: destructive without approval ─────────────────────────
deny_reason = "DENY: destructive action requires HITL" if {
    input.risk_level == "high"
    input.blast_radius >= 0.9
    not input.hitl_approved
    not is_production
}

# ── DENY: replica ceiling ──────────────────────────────────────
deny_reason = "DENY: replica count exceeds ceiling of 50" if {
    input.requested_replicas > 50
}

# ── DENY: latest tag ───────────────────────────────────────────
deny_reason = "DENY: :latest image tag forbidden" if {
    contains(input.prompt, ":latest")
}

# ── DENY: kube-system ──────────────────────────────────────────
deny_reason = "DENY: kube-system is immutable" if {
    contains(input.prompt, "kube-system")
}

# ── helper ─────────────────────────────────────────────────────
is_production if { contains(input.prompt, "production") }
is_production if { contains(input.prompt, "prod namespace") }
is_production if { contains(input.prompt, "prod database") }
is_production if { contains(input.prompt, "prod fleet") }
is_production if { contains(input.prompt, " prod ") }
