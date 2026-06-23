import streamlit as st
import os
import sys
import pandas as pd
import time
from datetime import datetime

# Add root folder to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import run_compare_tool, clear_cache, PROGRESS_CACHE_FILE
from config import CREDENTIALS

# Set up page configurations
st.set_page_config(
    page_title="电子元器件多渠道比价工具",
    page_icon="⭐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
    <style>
    .main-title {
        font-size: 2.5rem;
        color: #1F497D;
        text-align: center;
        margin-bottom: 0.5rem;
        font-weight: bold;
    }
    .sub-title {
        font-size: 1.1rem;
        color: #595959;
        text-align: center;
        margin-bottom: 2rem;
    }
    .stButton>button {
        background-color: #1F497D;
        color: white;
        border-radius: 5px;
        padding: 0.5rem 2rem;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #17375E;
        color: #E2EFDA;
    }
    </style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-title'>电子元器件价格与库存比价工具</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>上传 Excel 采购清单，在国内外主流渠道（立创商城、云汉芯城、华强电子网、Mouser、Digi-Key）中自动比价并高亮最低价</div>", unsafe_allow_html=True)

# ----------------- Sidebar Configurations -----------------
st.sidebar.header("⚙️ 运行参数配置")

# Choose active sites
st.sidebar.subheader("选择待查询的渠道")
active_sites = []
for site_id, config in CREDENTIALS.items():
    # Only list the 5 core sites for now
    if site_id in ["szlcsc", "ickey", "hqew", "mouser", "digikey"]:
        checked = st.sidebar.checkbox(config["name"], value=True, key=f"site_{site_id}")
        if checked:
            active_sites.append(site_id)

# Browser mode
st.sidebar.subheader("浏览器运行模式")
headless_option = st.sidebar.selectbox(
    "有无头模式 (推荐有头模式，以便手动滑块验证)",
    options=["有头模式 (可见窗口 - 推荐)", "无头模式 (后台运行)"],
    index=0
)
headless = (headless_option == "无头模式 (后台运行)")

# Clear cache utilities
st.sidebar.subheader("缓存管理")
cache_exists = os.path.exists(PROGRESS_CACHE_FILE)
if cache_exists:
    st.sidebar.write("⚡ 检测到上次未完成的抓取进度缓存。默认情况下，系统会自动**断点续传**（跳过已成功抓取的型号网站）。")
    if st.sidebar.button("🗑️ 清空所有缓存并重新抓取"):
        clear_cache()
        st.sidebar.success("缓存已成功清空！")
        time.sleep(1)
        st.rerun()
else:
    st.sidebar.write("💡 当前无抓取进度缓存，任务将从头开始抓取。")


# ----------------- Main Interface -----------------
# 1. Upload Excel File
uploaded_file = st.file_uploader(
    "1. 上传 Excel 采购清单 (第一行须为列名，必须包含：型号、采购数量；品牌为可选)", 
    type=["xlsx", "xls"]
)

if uploaded_file is not None:
    try:
        # Load preview of input excel
        df_preview = pd.read_excel(uploaded_file)
        
        st.subheader("BOM 清单预览")
        st.dataframe(df_preview.head(10), use_container_width=True)
        st.info(f"📋 共加载了 {len(df_preview)} 行元器件数据。")

        # Define temporary paths for processing
        temp_input_path = "temp_input_bom.xlsx"
        temp_output_path = "temp_output_compare.xlsx"

        # Save uploaded file to temp path
        with open(temp_input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        # 2. Trigger Comparison Run
        st.subheader("2. 开始比价流程")
        
        if not active_sites:
            st.error("❌ 请至少在侧边栏选择一个查询渠道！")
        else:
            col1, col2 = st.columns([1, 4])
            
            with col1:
                start_btn = st.button("🚀 开始自动比价", use_container_width=True)
                
            if start_btn:
                progress_bar = st.progress(0)
                status_text = st.empty()
                info_area = st.empty()
                
                # Callback to update progress bar and status texts
                def update_progress(current, total, message):
                    percent = current / total
                    progress_bar.progress(percent)
                    status_text.text(f"进度: {current}/{total} ({percent * 100:.1f}%)")
                    info_area.info(message)
                
                # Run the compare orchestrator
                try:
                    with st.spinner("爬虫运行中，请注意配合可能弹出的浏览器人机验证..."):
                        run_compare_tool(
                            temp_input_path, 
                            temp_output_path, 
                            active_sites=active_sites, 
                            headless=headless,
                            progress_callback=update_progress
                        )
                    
                    st.success("🎉 比价比对任务圆满完成！")
                    progress_bar.progress(100)
                    status_text.text("进度: 100/100% 已完成")
                    info_area.empty()
                    
                    # Read final results table for preview
                    if os.path.exists(temp_output_path):
                        df_result = pd.read_excel(temp_output_path)
                        
                        st.subheader("🏆 比价数据汇总预览")
                        # Highlight rows where '最低价' is '⭐'
                        def highlight_lowest(row):
                            return ['background-color: #E2EFDA' if row['最低价'] == '⭐' else '' for _ in row]
                        
                        st.dataframe(
                            df_result.style.apply(highlight_lowest, axis=1),
                            use_container_width=True
                        )

                        # Provide download button
                        with open(temp_output_path, "rb") as file:
                            btn = st.download_button(
                                label="📥 下载完整比价 Excel 报告 (已自动标记最低价)",
                                data=file,
                                file_name=f"元器件比价报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                
                except Exception as run_err:
                    st.error(f"❌ 运行过程中发生错误: {run_err}")
                
                finally:
                    # Clean up temporary input file
                    if os.path.exists(temp_input_path):
                        try:
                            os.remove(temp_input_path)
                        except Exception:
                            pass
                            
    except Exception as e:
        st.error(f"❌ 解析 Excel 文件失败: {e}")
else:
    st.write("👉 上传 Excel 清单后即可开始。您可以使用 [参考资料](file:///Users/adolf/Desktop/code/爬虫/参考资料/电子元器件采购清单.xlsx) 中的模板作为输入测试。")
