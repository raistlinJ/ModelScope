import streamlit as st
import time

st.title("Main App")
st.write(time.time())

@st.fragment
def my_frag():
    st.write("Fragment time:", time.time())
    time.sleep(0.5)
    st.rerun()

my_frag()
