import pandas as pd
from html_report_generator import HtmlReportGenerator


def test_html_report_generator_with_missing_avg_return(tmp_path):
    df = pd.DataFrame([
        {
            "symbol": "2330.TW",
            "name": "TSMC",
            "suitable": True,
            "score": 8.5,
            "volatility": 0.2,
            "win_rate": 0.6,
            "num_trades": 5,
            "total_return": 0.25
        }
    ])

    industry_results = {"科技股": df}
    generator = HtmlReportGenerator(output_dir=str(tmp_path))
    report_path = generator.generate_report(
        industry_results,
        analysis_params={"rsi_period": 14},
        start_date="2022-01-01",
        end_date="2022-12-31"
    )

    assert report_path is not None
    assert tmp_path.joinpath(report_path.split("/")[-1]).exists()
