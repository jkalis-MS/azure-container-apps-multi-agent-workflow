"""Pronunciation dictionary for TTS-friendly technical term replacement."""
import re

PRONUNCIATIONS = {
    "kubectl": "kube-control",
    "KEDA": "keh-dah",
    "Dapr": "dapper",
    "Bicep": "bye-sep",
    "WASI": "wah-zee",
    "gRPC": "gee-R-P-C",
    "Istio": "is-tee-oh",
    "Nginx": "engine-X",
    "Kubernetes": "koo-ber-net-ees",
    "PostgreSQL": "post-gres-Q-L",
    "SQLite": "S-Q-lite",
    "PyPI": "pie-P-I",
    "NuGet": "new-get",
    "OAuth": "oh-auth",
    "WebAssembly": "web assembly",
    "YAML": "yam-ul",
    "JSON": "jay-son",
    "CLI": "C-L-I",
    "SDK": "S-D-K",
    "API": "A-P-I",
    "URL": "U-R-L",
    "HTML": "H-T-M-L",
    "CSS": "C-S-S",
    "REST": "rest",
    "OTEL": "oh-tel",
    "OTLP": "O-T-L-P",
    "RBAC": "R-back",
    "CICD": "C-I-C-D",
    "VM": "V-M",
    "VNet": "V-net",
    "NSG": "N-S-G",
    "ACA": "A-C-A",
    "AKS": "A-K-S",
    "ACR": "A-C-R",
    "ARO": "A-R-O",
    "ACI": "A-C-I",
    "AAD": "A-A-D",
    "APIM": "A-P-I-M",
    "MSAL": "M-sal",
    "OIDC": "O-I-D-C",
    "JWT": "J-W-T",
    "CORS": "cores",
    "CNCF": "C-N-C-F",
    "WASM": "waz-um",
    "LLM": "L-L-M",
    "GPT": "G-P-T",
    "RAG": "rag",
    "TTS": "T-T-S",
    "STT": "S-T-T",
    "Azure": "azh-er",
    "IoT": "I-o-T",
    "SaaS": "sass",
    "PaaS": "pass",
    "IaaS": "I-ass",
    # Punctuated terms (regex handles these specially)
    ".NET": "dot-net",
    "C#": "C-sharp",
    "F#": "F-sharp",
    "Node.js": "node-J-S",
    "Next.js": "next-J-S",
    "Vue.js": "view-J-S",
}

_URL_PATTERN = re.compile(r'https?://\S+|`[^`]+`')


def apply_pronunciations(text: str) -> str:
    """Replace technical terms with TTS-friendly respellings.
    Protects URLs and inline code from replacement."""
    protected = {}

    def _protect(match):
        key = f"__PROTECTED_{len(protected)}__"
        protected[key] = match.group(0)
        return key

    text = _URL_PATTERN.sub(_protect, text)

    for term, pronunciation in PRONUNCIATIONS.items():
        # Handle punctuated terms (e.g., .NET, C#, Node.js) with explicit regex
        if any(c in term for c in '.#'):
            pattern = re.escape(term)
        else:
            pattern = rf'\b{re.escape(term)}\b'
        text = re.sub(pattern, pronunciation, text, flags=re.IGNORECASE)

    for key, original in protected.items():
        text = text.replace(key, original)
    return text
