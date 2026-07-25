from langchain_ollama import ChatOllama

try:
    llm = ChatOllama(model="tinyllama")
    response = llm.invoke("Bonjour, comment ça va ?")
    print("✅ Ollama fonctionne !")
    print(response.content)
except Exception as e:
    print(f"❌ Erreur: {e}")