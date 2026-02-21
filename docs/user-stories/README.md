# User Stories — FinanceSignal Investment Portal

## Overview

This directory contains user stories for building out FinanceSignal from a Reddit sentiment collection tool into a full-stack investment portal with a FastAPI backend and React frontend dashboard.

## Organization

Stories are numbered in dependency order — later stories build on earlier ones. They are grouped into phases:

| Phase | Stories | Focus |
|-------|---------|-------|
| 1 — Project Scaffolding | 01–02 | FastAPI + React project setup |
| 2 — App Shell & Layout  | 03–04 | Side nav, theming, health checks |
| 3 — Data API Layer      | 05–07 | Ticker, post, and subreddit endpoints |
| 4 — Scraper Integration | 08–09 | Background task, monitoring |
| 5 — Dashboard Views     | 10–13 | Trending, detail, feed, monitor panels |
| 6 — Subreddit Mgmt UI   | 14     | Subreddit CRUD page |
| 7 — Enhancements        | 15–20 | Sentiment, comparison, heatmap, export, watchlist, alerts |

## Story Format

Each story file contains:
- **Title and summary** — what the story delivers
- **Dependencies** — which stories must be completed first
- **Requirements** — detailed specification
- **Acceptance criteria** — checklist for "done"
- **Technical notes** — implementation hints and constraints

## Conventions

When implementing stories, follow these rules:
- Keep the directory structure neat and organized
- Update `requirements.txt` when adding Python packages
- Update these docs when making architectural changes
- Create/update the project README alongside implementation
- Append new feature ideas to `feature-ideas.md` and inform the user

## Status Tracking

| Story | Title | Status |
|-------|-------|--------|
| 01 | FastAPI Project Setup | done |
| 02 | React Frontend Init | done |
| 03 | App Shell & Side Nav | done |
| 04 | API Health & Config | done |
| 05 | Ticker Endpoints | not started |
| 06 | Post Endpoints | not started |
| 07 | Subreddit Management API | not started |
| 08 | Scraper as Background Task | not started |
| 09 | Scraper Monitoring Endpoint | not started |
| 10 | Trending Dashboard | not started |
| 11 | Ticker Detail View | not started |
| 12 | Post Feed Component | not started |
| 13 | Scraper Monitor Panel | not started |
| 14 | Subreddit Management Page | not started |
| 15 | Sentiment Score Display | not started |
| 16 | Ticker Comparison | not started |
| 17 | Subreddit Activity Heatmap | not started |
| 18 | Data Export | not started |
| 19 | Watchlist | not started |
| 20 | Mention Velocity Alerts | not started |
