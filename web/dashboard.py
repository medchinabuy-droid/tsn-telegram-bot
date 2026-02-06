from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from services.google_sheets import get_all

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse("""
<!DOCTYPE html>
<html>
<head>
  <title>Админка ТСН</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body style="font-family:Arial;padding:20px;">
  <h1>📊 Дашборд оплат</h1>
  <canvas id="chart"></canvas>

  <script>
    async function loadStats() {
      const res = await fetch('/admin/api/stats');
      const data = await res.json();

      new Chart(document.getElementById('chart'), {
        type: 'pie',
        data: {
          labels: ['Оплачено', 'Долг'],
          datasets: [{
            data: [data.paid, data.debt]
          }]
        }
      });
    }
    loadStats();
  </script>
</body>
</html>
""")

@router.get("/api/stats")
async def api_stats():
    rows = get_all()
    paid = sum(1 for r in rows if r.get("Статус") == "Оплачено")
    debt = len(rows) - paid
    return JSONResponse({"paid": paid, "debt": debt, "total": len(rows)})
