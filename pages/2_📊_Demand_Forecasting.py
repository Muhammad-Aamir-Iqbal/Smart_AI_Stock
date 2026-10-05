"""Demand forecasting and product-ranking workspace."""

from __future__ import annotations

from io import BytesIO

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from modules.calendar_utils import convert_gregorian_to_hijri_string, get_next_week_dates
from modules.data_loader import load_raw_data, standardize_sales_data
from modules.forecaster import predict_sales_with_daily_forecast


def sample_dataset() -> pd.DataFrame:
    """Create a small, realistic dataset so the workflow is instantly explorable."""
    products = [
        ("GRC-001", "Basmati Rice 5kg", "Rice", 16, 72, 180),
        ("GRC-002", "Milk 1L", "Dairy", 24, 85, 35),
        ("GRC-003", "Cooking Oil 5L", "Grocery", 11, 58, 210),
        ("GRC-004", "Sugar 5kg", "Grocery", 14, 95, 95),
        ("GRC-005", "Tea 900g", "Beverages", 9, 42, 260),
        ("GRC-006", "Wheat Flour 10kg", "Flour", 12, 110, 130),
        ("GRC-007", "Red Chilli 200g", "Spices", 7, 24, 55),
        ("GRC-008", "Biscuits Family Pack", "Snacks", 19, 140, 40),
    ]
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=28)
    rows = []
    for product_id, name, category, base_sales, stock, profit in products:
        for offset, sale_date in enumerate(dates):
            rows.append({
                "date": sale_date,
                "product_id": product_id,
                "product_name": name,
                "category": category,
                "units_sold": max(0, base_sales + ((offset * 3 + len(product_id)) % 11) - 5),
                "stock_on_hand": stock,
                "profit_per_unit": profit,
                "location": "Lahore",
            })
    return pd.DataFrame(rows)


def excel_export(data: pd.DataFrame) -> bytes:
    """Create an in-memory Excel download without writing user data to disk."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        data.to_excel(writer, index=False, sheet_name="Demand Forecast")
    return buffer.getvalue()


st.title("Demand Forecasting & Sales Ranking")
st.caption("Upload daily sales history, factor in next week's calendar, and turn demand into replenishment actions.")

with st.sidebar:
    st.header("Forecast data")
    uploaded_file = st.file_uploader("Upload sales data", type=["csv", "xlsx", "xls", "json", "db", "sqlite", "sqlite3"])
    if st.button("Load Sample Dataset", use_container_width=True):
        st.session_state["forecast_source"] = sample_dataset()
        st.session_state["uploaded_file_id"] = "sample"
        st.session_state.pop("processing_report", None)
        st.session_state.pop("forecast_results", None)
        st.session_state.pop("forecast_daily", None)
    st.caption("Supported: CSV, Excel, JSON, and SQLite database exports.")

upload_id = (getattr(uploaded_file, "name", ""), getattr(uploaded_file, "size", 0)) if uploaded_file else None
if uploaded_file is not None and st.session_state.get("uploaded_file_id") != upload_id:
    try:
        raw_data = load_raw_data(uploaded_file)
        clean_data, processing_report = standardize_sales_data(raw_data)
        st.session_state["forecast_source"] = clean_data
        st.session_state["processing_report"] = processing_report
        st.session_state["uploaded_file_id"] = upload_id
        st.session_state.pop("forecast_results", None)
        st.session_state.pop("forecast_daily", None)
    except (ValueError, TypeError) as exc:
        st.error(f"Could not process the upload: {exc}")

next_week = get_next_week_dates()
st.subheader("Next-week planning calendar")
calendar_preview = pd.DataFrame({
    "Gregorian date": next_week.strftime("%a, %d %b %Y"),
    "Hijri date": [convert_gregorian_to_hijri_string(value) for value in next_week],
})
st.dataframe(calendar_preview, use_container_width=True, hide_index=True)

source_data = st.session_state.get("forecast_source")
if source_data is None:
    st.info("Upload a sales export in the sidebar or load the sample dataset to begin.")
    st.stop()

report = st.session_state.get("processing_report")
if report:
    if report["status"] == "success_with_warnings":
        st.warning("Data standardized with notes: " + "; ".join(report["warnings"] or report["defaults_applied"]))
    else:
        st.success(f"Loaded and standardized {report['output_rows']:,} data rows.")

with st.expander("Preview input data", expanded=False):
    st.dataframe(source_data.head(25), use_container_width=True, hide_index=True)

if st.button("Run next-week predictions", type="primary"):
    try:
        with st.spinner("Forecasting demand and checking inventory coverage..."):
            weekly, daily = predict_sales_with_daily_forecast(source_data)
            st.session_state["forecast_results"] = weekly
            st.session_state["forecast_daily"] = daily
    except (ValueError, TypeError, RuntimeError) as exc:
        st.error(f"Predictions could not be completed: {exc}")

results = st.session_state.get("forecast_results")
if results is None:
    st.stop()
if results.empty:
    st.warning("No usable sales rows were found for forecasting.")
    st.stop()

st.subheader("Filter the decision view")
location_options = ["All locations", *sorted(results["location"].dropna().astype(str).unique())]
category_options = ["All categories", *sorted(results["category"].dropna().astype(str).unique())]
filter_location, filter_category = st.columns(2)
selected_location = filter_location.selectbox("Location", location_options)
selected_category = filter_category.selectbox("Category", category_options)
filtered_results = results.copy()
if selected_location != "All locations":
    filtered_results = filtered_results[filtered_results["location"] == selected_location]
if selected_category != "All categories":
    filtered_results = filtered_results[filtered_results["category"] == selected_category]
if filtered_results.empty:
    st.warning("No products match the selected filters.")
    st.stop()

total_demand = int(filtered_results["predicted_units_next_week"].sum())
total_profit = float(filtered_results["predicted_profit"].sum())
total_opportunity = float(filtered_results["restock_profit_opportunity"].sum())
restock_count = int(filtered_results["reorder_recommended"].sum())
kpi_one, kpi_two, kpi_three, kpi_four = st.columns(4)
kpi_one.metric("Predicted demand", f"{total_demand:,} units")
kpi_two.metric("Predicted profit", f"Rs. {total_profit:,.0f}")
kpi_three.metric("Restock alerts", f"{restock_count}")
kpi_four.metric("Potential gross profit", f"Rs. {total_opportunity:,.0f}")
if filtered_results["profit_per_unit"].le(0).all():
    st.warning("No profit data was uploaded. Financial calculations are shown as Rs. 0; add a profit_per_unit, unit_profit, margin, or profit column for real estimates.")

priority = filtered_results.sort_values(
    ["stock_shortfall", "restock_profit_opportunity", "predicted_units_next_week"],
    ascending=False,
    kind="stable",
)
st.subheader("Restock priority")
if priority["reorder_recommended"].any():
    first = priority[priority["reorder_recommended"]].iloc[0]
    st.success(
        f"Restock {first['product_name']} first: {int(first['stock_shortfall'])} units → "
        f"Rs. {first['restock_profit_opportunity']:,.0f} potential gross profit opportunity."
    )
display = priority[[
    "product_name", "predicted_units_next_week", "stock_on_hand", "stock_shortfall",
    "profit_per_unit", "restock_profit_opportunity", "reorder_recommended",
]].rename(columns={
    "product_name": "Product", "predicted_units_next_week": "Predicted Demand",
    "stock_on_hand": "Current Stock", "stock_shortfall": "Shortfall",
    "profit_per_unit": "Profit / Unit (Rs.)",
    "restock_profit_opportunity": "Profit Opportunity (Rs.)",
    "reorder_recommended": "Restock?",
})
st.dataframe(display, use_container_width=True, hide_index=True)

st.subheader("Demand and inventory outlook")
chart_one, chart_two = st.columns(2)
with chart_one:
    st.markdown("**Top 5 best sellers**")
    top_five = filtered_results.nlargest(5, "predicted_units_next_week").sort_values("predicted_units_next_week")
    best_seller_chart = px.bar(
        top_five, x="predicted_units_next_week", y="product_name", orientation="h",
        text="predicted_units_next_week", labels={
            "predicted_units_next_week": "Predicted units", "product_name": "Product",
        },
    )
    best_seller_chart.update_layout(height=360, showlegend=False, margin=dict(l=0, r=20, t=10, b=0), yaxis={"automargin": True})
    best_seller_chart.update_traces(textposition="outside", cliponaxis=False)
    st.plotly_chart(best_seller_chart, use_container_width=True)
with chart_two:
    st.markdown("**Estimated excess-stock exposure vs. profit opportunity**")
    excess_exposure = float((filtered_results["excess_stock_units"] * filtered_results["profit_per_unit"]).sum())
    pie_chart = go.Figure(data=[go.Pie(
        labels=["Estimated excess-stock exposure", "Profit opportunity protected through restocking"],
        values=[excess_exposure, total_opportunity],
        hole=0.58,
        textinfo="label+value",
        marker={"colors": ["#E6A23C", "#138A72"]},
    )])
    pie_chart.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    st.plotly_chart(pie_chart, use_container_width=True)
    st.caption("Estimate based on current stock versus forecast demand; it is not accounting-level waste.")

st.markdown("**Seven-day forecast**")
daily = st.session_state.get("forecast_daily", pd.DataFrame())
daily = daily[daily["product_id"].isin(filtered_results["product_id"])] if not daily.empty else daily
if not daily.empty:
    product_options = filtered_results.assign(
        selection=lambda frame: frame["product_name"] + " | " + frame["location"] + " | " + frame["product_id"]
    )
    chosen_product = st.selectbox("Product", product_options["selection"].tolist())
    chosen_row = product_options[product_options["selection"] == chosen_product].iloc[0]
    product_daily = daily[
        (daily["product_id"] == chosen_row["product_id"])
        & (daily["location"] == chosen_row["location"])
    ]
    line_chart = px.line(product_daily, x="date", y="predicted_units", markers=True,
                         labels={"date": "Date", "predicted_units": "Predicted units"})
    line_chart.update_layout(height=330, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(line_chart, use_container_width=True)

st.subheader("WhatsApp restock alert")
risk_products = priority[priority["reorder_recommended"]]
if risk_products.empty:
    st.info("There are no stockout-risk products in this filtered view.")
else:
    alert_product = st.selectbox("Product at stockout risk", risk_products["product_name"].tolist(), key="whatsapp_product")
    alert_row = risk_products[risk_products["product_name"] == alert_product].iloc[0]
    alert_message = (
        f"SmartStock AI Alert: {alert_row['product_name']} may stock out next week. "
        f"Current stock: {int(alert_row['stock_on_hand'])} units. "
        f"Predicted demand: {int(alert_row['predicted_units_next_week'])} units. "
        f"Suggested order: {int(alert_row['stock_shortfall'])} units. "
        f"Potential gross profit opportunity: Rs. {alert_row['restock_profit_opportunity']:,.0f}."
    )
    st.text_area("Prepared message", alert_message, height=110, disabled=True)
    phone_number = st.text_input("WhatsApp number (include country code)", placeholder="+923001234567")
    st.caption("Sending uses pywhatkit and WhatsApp Web on this computer; this is not WhatsApp Business API.")
    if st.button("Send WhatsApp Alert"):
        if not phone_number.strip().startswith("+") or not phone_number.strip()[1:].isdigit():
            st.error("Enter a valid international number, for example +923001234567.")
        else:
            try:
                import pywhatkit
                pywhatkit.sendwhatmsg_instantly(phone_number.strip(), alert_message, wait_time=15, tab_close=True)
                st.success("WhatsApp Web was asked to send the alert. Confirm delivery in WhatsApp.")
            except ImportError:
                st.warning("pywhatkit is not installed. The prepared message is still available above.")
            except Exception as exc:
                st.error(f"WhatsApp Web could not send the alert: {exc}")

download_csv, download_excel, _ = st.columns([1, 1, 2])
with download_csv:
    st.download_button(
        "Export CSV", data=priority.to_csv(index=False).encode("utf-8"),
        file_name="smartstock_demand_forecast.csv", mime="text/csv", use_container_width=True,
    )
with download_excel:
    st.download_button(
        "Export Excel", data=excel_export(priority), file_name="smartstock_demand_forecast.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True,
    )
