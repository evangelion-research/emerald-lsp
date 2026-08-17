from pygls.lsp.server import LanguageServer
from lsprotocol import types

# 1. Initialize the core LSP server
server = LanguageServer("emerald-lsp", "v0.1.0")

# 2. Register a feature (e.g., Code Autocomplete)
@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=["."]),
)
def completions(params: types.CompletionParams) -> types.CompletionList:
    """Returns static autocomplete options when trigger characters are pressed."""
    
    # Grab the document being edited
    doc = server.workspace.get_text_document(params.text_document.uri)
    
    # Return completion items
    return types.CompletionList(
        is_incomplete=False,
        items=[
            types.CompletionItem(
                label="helloWorld",
                kind=types.CompletionItemKind.Function,
                detail="Custom LSP Demo function",
                documentation="This is a test autocomplete item from your custom Python LSP."
            ),
            types.CompletionItem(
                label="goodbyeWorld",
                kind=types.CompletionItemKind.Method,
                detail="Another custom function"
            )
        ]
    )

if __name__ == "__main__":
    # 3. Start the server using standard Input/Output streams
    server.start_io()
