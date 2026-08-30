import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "backend")

import chatbot_pipeline as cp

store = json.load(open("backend/chat_store.json", encoding="utf-8"))
file_content = None
for msg in store.get("messages", []):
    if msg.get("file_name") == "IEEE_Emotion_Detection_Paper.docx":
        file_content = msg.get("file_content")
        break

print("FILE_CONTENT_LEN:", len(file_content) if file_content else None)
print("CONTAINS 'urinary':", "urinary" in (file_content or "").lower())

file_name = "IEEE_Emotion_Detection_Paper.docx"
user_question = "what is the info present in these doc"
llm_query = (
    f"The user has attached a file named '{file_name}'. "
    f"Here is its content:\n\n{file_content}\n\n"
    f"User's question: {user_question}"
)

print("\n--- resolve_disease on the combined query ---")
disease, status = cp.resolve_disease(llm_query, cp.DISEASE_INDEX, None)
print("DISEASE:", disease, "STATUS:", status)

print("\n--- classify_question_level ---")
print("LEVEL:", cp.classify_question_level(llm_query))

print("\n--- match_disease raw ---")
print("MATCH:", cp.match_disease(llm_query, cp.DISEASE_INDEX))