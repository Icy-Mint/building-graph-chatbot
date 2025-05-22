import streamlit as st
st.set_page_config(page_title="Building Sensor Chatbot", page_icon="🏢")

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain.chains.graph_qa.cypher import GraphCypherQAChain

# Load environment
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD")

# Initialize graph and model
graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASS)
llm = ChatOpenAI(model="gpt-4", temperature=0, openai_api_key=OPENAI_API_KEY)
chain = GraphCypherQAChain.from_llm(llm=llm, graph=graph, verbose=True, allow_dangerous_requests=True)

# UI
st.title("🏢 Building AI Chatbot - Sensor Graph")
user_question = st.text_input("Ask about rooms, AC units, or sensors:")

if user_question:
    with st.spinner("Thinking..."):
        try:
            response = chain.invoke({"query": user_question})
            cypher = response.get("cypher", "").strip()
            answer = response.get("result", "")

            # Show the generated Cypher code
            with st.expander("🧠 Generated Cypher"):
                st.code(cypher, language="cypher")

            # BLOCK execution of bad cypher
            if not cypher.lower().startswith(("match", "with", "call")):
                st.warning("⚠️ This type of question cannot be answered using the current database structure.")
                st.info(answer)
            else:
                st.success("Answer:")
                st.write(answer)

        except Exception as e:
            st.error(f"❌ Something went wrong:\n\n{e}")
