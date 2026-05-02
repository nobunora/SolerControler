from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

from app.dashboard_data import load_dashboard_data


def _fallback_days(n: int = 14) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _align_dual_axis(ax_left, ax_right, bins: int = 6) -> None:
    ax_left.yaxis.set_major_locator(MaxNLocator(nbins=bins))
    ax_right.yaxis.set_major_locator(MaxNLocator(nbins=bins))
    ax_left.grid(axis="y", alpha=0.35)


def main() -> int:
    db_path = Path("artifacts/solar_monitor.db")
    out_path = Path("artifacts/dashboard_mock.png")
    data = load_dashboard_data(db_path)

    sunshine = data.sunshine_daily
    if sunshine:
        days = [r["date"] for r in sunshine]
        forecast = [r.get("forecast_hours") for r in sunshine]
        actual = [r.get("actual_hours") for r in sunshine]
        diff = [(a or 0) - (f or 0) for a, f in zip(actual, forecast)]
    else:
        days = _fallback_days()
        forecast = [4.5 + (i % 5) * 0.5 for i in range(len(days))]
        actual = [v + (-0.8 + (i % 4) * 0.4) for i, v in enumerate(forecast)]
        diff = [a - f for a, f in zip(actual, forecast)]

    cost_daily = data.cost_daily
    if cost_daily:
        c_days = [r["date"] for r in cost_daily]
        self_kwh = [r["self_consumption_kwh"] for r in cost_daily]
        yen = [r["savings_yen"] for r in cost_daily]
        c_kwh = [r["cumulative_kwh"] for r in cost_daily]
        c_yen = [r["cumulative_yen"] for r in cost_daily]
    else:
        c_days = days
        self_kwh = [8 + (i % 6) * 0.9 for i in range(len(c_days))]
        yen = [x * 31 for x in self_kwh]
        c_kwh = []
        c_yen = []
        acc_kwh = 0.0
        acc_yen = 0.0
        for k, y in zip(self_kwh, yen):
            acc_kwh += k
            acc_yen += y
            c_kwh.append(acc_kwh)
            c_yen.append(acc_yen)

    cost_monthly = data.cost_monthly
    if cost_monthly:
        m_labels = [r["month"] for r in cost_monthly]
        m_kwh = [r["self_consumption_kwh"] for r in cost_monthly]
        m_yen = [r["savings_yen"] for r in cost_monthly]
    else:
        m_labels = ["2026-03", "2026-04", "2026-05"]
        m_kwh = [220, 265, 284]
        m_yen = [6820, 8215, 8804]

    battery = data.battery_daily
    if battery:
        b_days = [r["date"] for r in battery]
        b_target = [r["setting_soc_target_percent"] for r in battery]
        b_night = [r["night_charge_kwh"] for r in battery]
        b_pv_max = [r["pv_max_charge_kwh"] for r in battery]
        b_end = [r["end_of_day_soc_percent"] for r in battery]
    else:
        b_days = days
        b_target = [35 + (i % 3) * 5 for i in range(len(days))]
        b_night = [1.2 + (i % 4) * 0.4 for i in range(len(days))]
        b_pv_max = [2.5 + (i % 5) * 0.5 for i in range(len(days))]
        b_end = [25 + (i % 4) * 8 for i in range(len(days))]

    params = data.model_parameters
    if not params:
        params = [
            {"name": "soc_per_kwh_charge", "mean_value": 10.7, "variance": 0.21, "sample_count": 30, "hit_rate": 0.87},
            {"name": "soc_per_kwh_discharge", "mean_value": 12.1, "variance": 0.24, "sample_count": 30, "hit_rate": 0.85},
            {"name": "pv_kwh_per_sunhour", "mean_value": 1.45, "variance": 0.08, "sample_count": 30, "hit_rate": 0.81},
            {"name": "pv_temp_coeff_per_deg", "mean_value": -0.0035, "variance": 0.0002, "sample_count": 30, "hit_rate": 0.78},
            {"name": "battery_temp_coeff_per_deg", "mean_value": -0.0050, "variance": 0.0003, "sample_count": 30, "hit_rate": 0.76},
        ]

    plt.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "DejaVu Sans"]
    fig = plt.figure(figsize=(18, 12), facecolor="#f3f9ff")
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.95], hspace=0.35, wspace=0.22)

    ax1 = fig.add_subplot(gs[0, 0])
    ax1.plot(days, forecast, label="予測(時間)", color="#147efb", linewidth=2)
    ax1.plot(days, actual, label="実績(時間)", color="#14b86f", linewidth=2)
    ax1b = ax1.twinx()
    ax1b.bar(days, diff, alpha=0.3, label="差分(時間)", color="#ef8e1d")
    ax1.set_title("1) 日照時間 予測と実績")
    ax1.set_ylabel("時間")
    ax1.tick_params(axis="x", rotation=45)
    _align_dual_axis(ax1, ax1b)
    lines1 = ax1.get_lines() + ax1b.containers
    labels1 = ["予測(時間)", "実績(時間)", "差分(時間)"]
    ax1.legend(lines1, labels1, loc="upper left")

    ax2 = fig.add_subplot(gs[0, 1])
    ax2.plot(c_days, self_kwh, label="自家消費(kWh)", color="#147efb", linewidth=2)
    ax2.plot(c_days, c_kwh, label="累計(kWh)", color="#14b86f", linewidth=2, linestyle="--")
    ax2b = ax2.twinx()
    ax2b.plot(c_days, yen, label="節約額(円)", color="#ef8e1d", linewidth=2)
    ax2b.plot(c_days, c_yen, label="累計(円)", color="#e6504f", linewidth=2, linestyle="--")
    ax2.set_title("2) 自家消費と節約額（日）")
    ax2.set_ylabel("kWh")
    ax2b.set_ylabel("円")
    ax2.tick_params(axis="x", rotation=45)
    _align_dual_axis(ax2, ax2b)
    lines2 = ax2.get_lines() + ax2b.get_lines()
    ax2.legend(lines2, [l.get_label() for l in lines2], loc="upper left")

    ax3 = fig.add_subplot(gs[1, 0])
    ax3.plot(m_labels, m_kwh, marker="o", color="#147efb", label="月間 自家消費(kWh)")
    ax3b = ax3.twinx()
    ax3b.plot(m_labels, m_yen, marker="o", color="#ef8e1d", label="月間 節約額(円)")
    ax3.set_title("3) 自家消費と節約額（月）")
    ax3.set_ylabel("kWh")
    ax3b.set_ylabel("円")
    _align_dual_axis(ax3, ax3b)
    lines3 = ax3.get_lines() + ax3b.get_lines()
    ax3.legend(lines3, [l.get_label() for l in lines3], loc="upper left")

    ax4 = fig.add_subplot(gs[1, 1])
    ax4.plot(b_days, b_night, label="夜間充電量(kWh)", color="#ef8e1d")
    ax4.plot(b_days, b_pv_max, label="太陽光 最大蓄電量(kWh)", color="#14b86f")
    ax4b = ax4.twinx()
    ax4b.plot(b_days, b_target, label="設定SOC(%)", color="#147efb")
    ax4b.plot(b_days, b_end, label="日終SOC(%)", color="#e6504f")
    ax4.set_title("4) 蓄電池設定値と実績")
    ax4.set_ylabel("kWh")
    ax4b.set_ylabel("%")
    ax4.tick_params(axis="x", rotation=45)
    _align_dual_axis(ax4, ax4b)
    lines4 = ax4.get_lines() + ax4b.get_lines()
    ax4.legend(lines4, [l.get_label() for l in lines4], loc="upper left")

    ax5 = fig.add_subplot(gs[2, :])
    ax5.axis("off")
    ax5.set_title("5) 蓄電池方程式とパラメータ", loc="left", fontsize=13, fontweight="bold")
    equation = (
        "目標エネルギー = 朝に足りない分 + 昼の余剰分を考慮して決定\n"
        "夜間充電量(kWh) = max(0, (目標 - 現在) / 充電効率)\n"
        "太陽光発電(kWh) = 日照時間 × 発電係数 × 温度係数"
    )
    ax5.text(0.01, 0.80, equation, fontsize=10)

    cell_text = []
    for p in params[:8]:
        hit_rate = p.get("hit_rate")
        hit_text = "-" if hit_rate is None else f"{float(hit_rate) * 100:.1f}%"
        cell_text.append(
            [
                str(p.get("name", "")),
                f"{float(p.get('mean_value', 0)):.5f}",
                f"{float(p.get('variance', 0)):.6f}",
                str(p.get("sample_count", "")),
                hit_text,
            ]
        )
    table = ax5.table(
        cellText=cell_text,
        colLabels=["パラメータ", "中心値", "分散", "サンプル数", "的中率"],
        loc="lower left",
        cellLoc="left",
        colLoc="left",
        bbox=[0.01, 0.02, 0.98, 0.64],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)

    fig.suptitle("太陽光 + 蓄電池 ダッシュボード（日本語モック）", fontsize=20, fontweight="bold", y=0.99)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
