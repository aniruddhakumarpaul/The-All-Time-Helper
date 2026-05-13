import os
import sys
import ast
import re

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.memory import index_document, user_context

# Target User for Global Indexing
TARGET_USER = "aniruddha24680kumarpaul@gmail.com"
user_context.set(TARGET_USER)

# Configuration
IGNORE_DIRS = {'.project_brain', '__pycache__', 'node_modules', '.git', 'venv', 'practice', '_archived_modules', 'scratch'}
SUPPORTED_EXTENSIONS = {'.py', '.js', '.css', '.html', '.md'}


# --- AST-Aware Chunkers ---

def chunk_code_python(content: str, file_path: str) -> list:
    """Parse Python files into function/class-level semantic chunks using the AST module."""
    chunks = []
    try:
        tree = ast.parse(content)
        lines = content.split('\n')
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno - 1
                end = node.end_lineno  # Python 3.8+
                body = '\n'.join(lines[start:end])
                
                # Skip trivial bodies (< 3 lines of actual content)
                if len(body.strip().split('\n')) < 3:
                    continue
                
                # Cap oversized chunks to prevent embedding bloat
                if len(body) > 3000:
                    body = body[:3000]
                
                chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"
                chunks.append({
                    "content": body,
                    "metadata": {
                        "type": "code",
                        "subtype": chunk_type,
                        "name": node.name,
                        "file": file_path,
                        "line_start": node.lineno,
                        "line_end": node.end_lineno
                    }
                })
        
        # Fallback: if AST found nothing (e.g. module-level script), index the module head
        if not chunks and content.strip():
            chunks.append({
                "content": content[:2000],
                "metadata": {"type": "code", "subtype": "module", "name": "module_level", "file": file_path}
            })
    except SyntaxError:
        # Broken Python — fall back to paragraph-based chunking
        chunks = chunk_text_smart(content, file_path)
    
    return chunks


def chunk_code_js(content: str, file_path: str) -> list:
    """Parse JS files using regex-based function/class extraction."""
    chunks = []
    # Match: function name(...), const name = (...) =>, class Name {, async function name(...)
    pattern = r'(?:(?:async\s+)?function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\(.*?\)\s*=>|class\s+\w+)'
    
    matches = list(re.finditer(pattern, content))
    if not matches:
        return chunk_text_smart(content, file_path)
    
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        
        if len(body) < 50:  # Skip trivial fragments
            continue
        if len(body) > 3000:  # Cap oversized
            body = body[:3000]
        
        name_match = re.search(r'(?:function|const|let|var|class)\s+(\w+)', body)
        chunks.append({
            "content": body,
            "metadata": {
                "type": "code",
                "subtype": "function",
                "name": name_match.group(1) if name_match else "anonymous",
                "file": file_path
            }
        })
    return chunks


def chunk_text_smart(content: str, file_path: str, max_size: int = 1500) -> list:
    """Fallback chunker: splits by double-newline paragraphs instead of arbitrary char count.
    Used for .html, .css, .md, and files that fail AST/regex parsing."""
    paragraphs = re.split(r'\n\s*\n', content)
    chunks = []
    current = ""
    
    for para in paragraphs:
        if len(current) + len(para) > max_size and current:
            chunks.append({"content": current.strip(), "metadata": {"type": "code", "file": file_path}})
            current = para
        else:
            current += "\n\n" + para
    
    if current.strip():
        chunks.append({"content": current.strip(), "metadata": {"type": "code", "file": file_path}})
    
    return chunks


# --- Chunker Router ---

CHUNKERS = {
    '.py': chunk_code_python,
    '.js': chunk_code_js,
}
# .css, .html, .md all use chunk_text_smart (default)


def rebuild_neural_memory():
    """Walks the project and indexes all relevant source files using AST-aware chunking."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    count = 0
    errors = 0
    
    print(f"DEBUG: Starting AST-Aware Neural Memory Indexing in: {project_root}")
    
    for root, dirs, files in os.walk(project_root):
        # Filter directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext not in SUPPORTED_EXTENSIONS:
                continue
                
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, project_root)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Skip empty files
                if not content.strip():
                    continue
                
                # Route to the appropriate chunker
                chunker = CHUNKERS.get(ext, chunk_text_smart)
                chunks = chunker(content, rel_path)
                
                for i, chunk in enumerate(chunks):
                    name = chunk["metadata"].get("name", f"chunk_{i}")
                    doc_id = f"{rel_path}::{name}_{i}"
                    index_document(doc_id, chunk["content"], chunk["metadata"])
                    count += 1
                    
            except Exception as e:
                errors += 1
                print(f"WARNING: Error indexing {rel_path}: {e}")

    # FLAW 5 FIX: Index Manual Tool Rules
    MANUAL_TOOL_RULES = [
        {
            "name": "email_fidelity",
            "content": "FIDELITY RULE: When the user provides a technical block of text for an email (e.g. Shopify workflow), pass it verbatim to the 'raw_attachment_text' parameter. This ensures the full content reaches the recipient without being summarized or truncated by the LLM.",
            "tool": "send_email"
        },
        {
            "name": "email_tone",
            "content": "TONE RULE: If the user mentions 'formal', 'office', or 'broadcast', you MUST set tone='formal'. This triggers the Executive Correspondence template.",
            "tool": "send_email"
        },
        {
            "name": "email_broadcast",
            "content": "BROADCAST RULE: When sending to multiple recipients, write the email body WITHOUT any opening salutation (no 'Hi', 'Dear', 'Hello'). Start directly with the first sentence. The send_email_tool will automatically inject a personalized 'Dear [Name]' for each recipient.",
            "tool": "send_email"
        }
    ]
    
    for rule in MANUAL_TOOL_RULES:
        doc_id = f"rule_{rule['name']}"
        index_document(doc_id, rule["content"], {"type": "tool_rule", "tool": rule["tool"]})
        count += 1

    print(f"\nSUCCESS: Indexed {count} semantic chunks (AST-aware + {len(MANUAL_TOOL_RULES)} rules). Errors: {errors}")


if __name__ == "__main__":
    rebuild_neural_memory()
