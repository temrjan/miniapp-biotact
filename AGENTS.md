# AGENTS.md

Instructions for AI coding agents and contributors working in this repository.

This repository is **public**. Everything committed here, including commit
messages and pull request descriptions, can be read by anyone and stays in the
history even after a file is deleted.

## Keep private material out

- Plans, specifications, reviews, reports, audits, task state, and decision
  records live only in the maintainers' private documentation (DocHub). Do not
  add them to this repository, in any folder.
- Server details live only there too: IP addresses, SSH ports, users and key
  names, backup locations, and the state of server hardening. Do not add them
  to code, configuration, comments, or documentation here. A server path may
  appear only where code, its tests, or the feature README of that code need
  it (for example the deploy workflow, `ecosystem.config.js`, `ops/`).
- Never commit secrets: `.env` files, tokens, passwords, private keys. An
  `.env.example` holds placeholders only. In tests, write fake secrets as a
  short repeating pattern so that the secret scanner does not fire on them.
- Do not commit personal data: personal email addresses, phone numbers,
  customer data, or screenshots that show them.

## Commit messages and pull requests

- Describe the mechanics of a change: what the code did before and what it
  does now. Do not describe its consequences for customers, partners, or
  production, and do not link private documents.
- Use Conventional Commits: `<type>: <short description>`.

## Security

Report vulnerabilities as described in [SECURITY.md](SECURITY.md), never in a
public issue.
