
import streamlit as st
import requests

st.title("AI Fund Dashboard")

try:
    data=requests.get("http://localhost:8000").json()
    st.json(data)
except:
    st.write("API not ready")
