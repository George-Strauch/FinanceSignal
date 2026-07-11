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
| 4 — Process Integration | 08–09 | Background task, process monitoring |
| 5 — Dashboard Views     | 10–13 | Trending, detail, feed, process monitor panel |
| 6 — Subreddit Mgmt UI   | 14     | Subreddit CRUD page |
| 7 — Enhancements        | 15–20 | Sentiment, comparison, heatmap, export, watchlist, alerts |
| 8 — Historical Analysis | 22    | Historical evaluations, ticker discovery, extended windows |

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
| 05 | Ticker Endpoints | done |
| 06 | Post Endpoints | done |
| 07 | Subreddit Management API | done |
| 08 | Scraper as Background Task (via Process Manager) | done |
| 09 | Process Monitoring Endpoints | done |
| 10 | Trending Dashboard | done |
| 11 | Ticker Detail View | done |
| 12 | Post Feed Component | done |
| 13 | Process Monitor Panel | done |
| 14 | Subreddit Management Page | done |
| 15 | Sentiment Score Display | not started |
| 16 | Ticker Comparison | not started |
| 17 | Subreddit Activity Heatmap | not started |
| 18 | Data Export | not started |
| 19 | Watchlist | not started |
| 20 | Mention Velocity Alerts | not started |
| 22 | Historical Evaluations & Time-Aware Discovery | not started |
