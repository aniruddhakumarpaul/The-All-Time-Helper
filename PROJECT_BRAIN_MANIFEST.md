# Project Manifest: The All Time Helper

## Overview
A pro-grade Agentic Assistant with a 3D interactive mascot and a hierarchical agent swarm.

## Neural Memory (RAG)
- **Engine**: ChromaDB (Local Persistent)
- **Path**: `.project_brain/`
- **Primary Tools**: `recall_memory`, `archive_insight`
- **Strategy**: Always use `recall_memory` for architectural context to save user tokens.

## Key Architectural Decisions
1. **Mascot Interaction**: "Teacher Peek" 3D tilt with a 2s idle settle and 5s ultra-lazy return transition.
2. **One-Word Mode**: Hardened at the Identity Level (Manager Agent goal injection).
3. **Agentic Swarm**: Uses CrewAI with Process.hierarchical for complex tasks.
4. **Local Fallback**: Routes sensitive or privacy-focused queries to local 'helper' (Ollama) model.

## User Preferences
- **Tone**: Professional, technical, but encouraging.
- **Efficiency**: Minimize token usage by using the semantic index instead of raw file reads.
