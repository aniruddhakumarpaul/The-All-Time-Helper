import os
import sys

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.logic.memory import index_document

# Configuration
IGNORE_DIRS = {'.project_brain', '__pycache__', 'node_modules', '.git', 'venv', 'practice'}
SUPPORTED_EXTENSIONS = {'.py', '.js', '.css', '.html', '.md'}

def chunk_text(text, chunk_size=1500):
    """Splits text into chunks to ensure better semantic retrieval."""
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

def rebuild_neural_memory():
    """Walks the project and indexes all relevant source files."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    count = 0
    
    print(f"DEBUG: Starting Neural Memory Indexing in: {project_root}")
    
    for root, dirs, files in os.walk(project_root):
        # Filter directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in SUPPORTED_EXTENSIONS:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, project_root)
                
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    # Chunk and index
                    chunks = chunk_text(content)
                    for i, chunk in enumerate(chunks):
                        doc_id = f"{rel_path}_chunk_{i}"
                        metadata = {
                            "file": rel_path,
                            "type": "source_code",
                            "ext": ext,
                            "chunk": i
                        }
                        index_document(doc_id, chunk, metadata)
                        count += 1
                        
                except Exception as e:
                    print(f"WARNING: Error indexing {rel_path}: {e}")

    print(f"SUCCESS: Indexed {count} semantic chunks into the Project Brain.")

if __name__ == "__main__":
    rebuild_neural_memory()
