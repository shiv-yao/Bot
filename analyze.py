from app.analysis import generate_report
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/trades.json"
report = generate_report(path)
print(report.to_json())
