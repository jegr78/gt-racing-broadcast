"""Optional live checks for an explicitly supplied disposable Companion instance."""

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
RPC = """([method,path,input]) => new Promise((resolve,reject)=>{
  const ws = new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/trpc');
  const timer=setTimeout(()=>{ws.close();reject(Error('Companion RPC timeout'));},5000);
  ws.onopen=()=>ws.send(JSON.stringify({id:999,method,params:{path,input}}));
  ws.onmessage=event=>{let rows=JSON.parse(event.data);if(!Array.isArray(rows))rows=[rows];
    for(const row of rows){if(row.id!==999)continue;
      if(row.error){clearTimeout(timer);ws.close();reject(Error(JSON.stringify(row.error)));}
      else if(row.result?.type==='data'){clearTimeout(timer);ws.close();resolve(row.result.data);}}};
  ws.onerror=()=>{clearTimeout(timer);reject(Error('Companion WebSocket failed'));};
})"""


def _caption(page, style):
    return page.evaluate(
        RPC,
        [
            "subscription",
            "preview.expressionStream.watchExpression",
            {
                "expression": style["text"],
                "controlId": None,
                "isVariableString": not style.get("textExpression", False),
                "requiredType": "string",
            },
        ],
    )


def _wait_caption(page, style, expected):
    for _ in range(24):
        result = _caption(page, style)
        if result == {"ok": True, "value": expected}:
            return
        page.wait_for_timeout(250)
    raise AssertionError((expected, result))


def run(pw, url, relay_port, rename, obs, directory, shots=False):
    """Full-import only into the caller's disposable instance; OBS is disabled."""
    config = json.loads(
        (ROOT / "src/companion/racecast-buttons.companionconfig").read_text(
            encoding="utf-8"
        )
    )
    for instance in config["instances"].values():
        if instance["moduleId"] == "obs-studio":
            instance["enabled"] = False
            instance["config"].update(host="127.0.0.1", port=9, password="")

    def redirect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key == "url"
                    and isinstance(child, dict)
                    and isinstance(child.get("value"), str)
                ):
                    child["value"] = child["value"].replace(
                        "127.0.0.1:8088", f"127.0.0.1:{relay_port}"
                    )
                else:
                    redirect(child)
        elif isinstance(value, list):
            for child in value:
                redirect(child)

    redirect(config)
    file = Path(directory) / "alias-buttons.companionconfig"
    file.write_text(json.dumps(config), encoding="utf-8")
    context = pw.chromium.launch_persistent_context(
        str(Path(directory) / "companion-browser"),
        headless=True,
        viewport={"width": 1280, "height": 1000},
    )
    try:
        page = context.pages[0]
        page.goto(url + "/import-export")
        page.locator("input[type=file]").first.wait_for(state="attached")
        close = page.get_by_role("button", name="Close modal", exact=True)
        if close.count():
            close.click()
        page.locator("input[type=file]").first.set_input_files(str(file))
        page.get_by_role("button", name="Import Preserving Unselected").click()
        page.wait_for_timeout(1200)
        page.goto(url + "/tablet?pages=3")
        target = page.locator('.button-border[title="Button 3/3/0"]')
        target.wait_for()
        style = config["pages"]["3"]["controls"]["3"]["0"]["style"]
        _wait_caption(page, style, "Community standings")
        before = target.evaluate("(e)=>getComputedStyle(e).backgroundImage")
        rename("Sponsor board")
        _wait_caption(page, style, "Sponsor board")
        page.wait_for_function(
            "([before]) => getComputedStyle(document.querySelector('.button-border[title=\"Button 3/3/0\"]')).backgroundImage !== before",
            arg=[before],
        )
        target.click()
        page.wait_for_timeout(300)
        assert ("item", "Stint", "Standings", True) in obs.calls, obs.calls
        rename("$(internal:time_hms)")
        _wait_caption(page, style, "$(internal:time_hms)")
        rename("")
        _wait_caption(page, style, "Standings Toggle")
        page.evaluate(
            RPC,
            ["mutation", "customVariables.delete", {"name": "racecast_asset_labels"}],
        )
        _wait_caption(page, style, "Standings Toggle")
        page.evaluate(
            RPC,
            [
                "mutation",
                "customVariables.create",
                {"name": "racecast_asset_labels", "defaultVal": ""},
            ],
        )
        page.evaluate(
            RPC,
            [
                "mutation",
                "customVariables.setDefault",
                {"name": "racecast_asset_labels", "value": {}},
            ],
        )
        page.evaluate(
            RPC,
            [
                "mutation",
                "customVariables.setCurrent",
                {"name": "racecast_asset_labels", "value": {}},
            ],
        )
        rename()
        _wait_caption(page, style, "Community standings")
        if shots:
            for number, name in [
                (1, "companion-page1-show-control.png"),
                (3, "companion-page3-flags-graphics.png"),
            ]:
                page.goto(url + f"/tablet?pages={number}")
                page.locator(".button-control.clickable").first.wait_for()
                page.wait_for_timeout(400)
                boxes = page.locator(".button-control").evaluate_all(
                    "(es)=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};})"
                )
                left = min(b["x"] for b in boxes)
                top = min(b["y"] for b in boxes)
                clip = {
                    "x": left,
                    "y": top,
                    "width": max(b["right"] for b in boxes) - left,
                    "height": max(b["bottom"] for b in boxes) - top,
                }
                destination = ROOT / "src/docs/wiki/images" / name
                page.screenshot(path=str(destination), clip=clip)
                slide = ROOT / "src/docs/slides/assets/img" / name
                if slide.exists():
                    shutil.copy2(destination, slide)
        print(
            "PASS native Companion: polled aliases, rendered image changes, canonical click, literal variable text, removal/missing-map fallback"
        )
    finally:
        context.close()
