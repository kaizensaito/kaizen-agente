import sys, os

print("🧱 Estrutura de diretórios:")
print(os.listdir(os.path.abspath(os.path.dirname(__file__))))
print("\n🧭 sys.path:")
for p in sys.path:
    print("  →", p)

print("\n📁 modules/ existe?", os.path.exists(os.path.join(os.path.dirname(__file__), '..', 'modules')))
print("📄 llm.py existe?", os.path.exists(os.path.join(os.path.dirname(__file__), '..', 'modules', 'llm.py')))
