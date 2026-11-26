"""Compile Windows UIA elements, AppProfile regions, and cursor position into a coordinate-indexed ASCII map."""

from __future__ import annotations

from kinetic_mcp.models import AppProfile, PixelBox, ScreenGridSnapshot, UIElement


def pixel_to_grid(
    x: int,
    y: int,
    bounds: PixelBox,
    grid_w: int,
    grid_h: int,
) -> tuple[int, int]:
    """Map physical screen pixel (x, y) to ASCII grid cell (col, row)."""
    if bounds.width <= 0 or bounds.height <= 0 or grid_w <= 0 or grid_h <= 0:
        return (0, 0)
    rx = (x - bounds.left) / bounds.width
    ry = (y - bounds.top) / bounds.height
    col = min(grid_w - 1, max(0, int(rx * grid_w)))
    row = min(grid_h - 1, max(0, int(ry * grid_h)))
    return col, row


def grid_to_pixel(
    col: int,
    row: int,
    bounds: PixelBox,
    grid_w: int,
    grid_h: int,
) -> tuple[int, int]:
    """Map ASCII grid cell (col, row) to physical pixel coordinates at cell center."""
    if grid_w <= 0 or grid_h <= 0:
        return bounds.center
    c = min(grid_w - 1, max(0, col))
    r = min(grid_h - 1, max(0, row))
    px = bounds.left + round(((c + 0.5) / grid_w) * bounds.width)
    py = bounds.top + round(((r + 0.5) / grid_h) * bounds.height)
    return bounds.clamp_point(px, py)


def _short_tag(elem_id: str, label: str, max_len: int = 13) -> str:
    clean = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in label.strip())
    clean = clean.strip("_") or "elem"
    room = max(3, max_len - len(elem_id) - 3)
    return f"[{elem_id}:{clean[:room]}]"


def _draw_region_outline(
    canvas: list[list[str]],
    box: PixelBox,
    bounds: PixelBox,
    grid_w: int,
    grid_h: int,
) -> None:
    c1, r1 = pixel_to_grid(box.left, box.top, bounds, grid_w, grid_h)
    c2, r2 = pixel_to_grid(max(box.left, box.right - 1), max(box.top, box.bottom - 1), bounds, grid_w, grid_h)
    if c2 - c1 < 2 or r2 - r1 < 2:
        return
    for c in range(c1 + 1, c2):
        if canvas[r1][c] == " ":
            canvas[r1][c] = "-"
        if canvas[r2][c] == " ":
            canvas[r2][c] = "-"
    for r in range(r1 + 1, r2):
        if canvas[r][c1] == " ":
            canvas[r][c1] = "|"
        if canvas[r][c2] == " ":
            canvas[r][c2] = "|"
    for cr, cc in ((r1, c1), (r1, c2), (r2, c1), (r2, c2)):
        if canvas[cr][cc] in (" ", "-", "|"):
            canvas[cr][cc] = "+"


def _place_tag(
    canvas: list[list[str]],
    occupied: list[list[bool]],
    col: int,
    row: int,
    tag: str,
    grid_w: int,
    grid_h: int,
) -> None:
    if len(tag) >= grid_w:
        tag = tag[: max(1, grid_w - 1)]
    start_col = min(max(0, col - len(tag) // 2), max(0, grid_w - len(tag)))

    # Search nearby rows if exact slot already holds another tag
    row_offsets = [0, 1, -1, 2, -2]
    chosen_row = row
    chosen_col = start_col
    for dr in row_offsets:
        rr = row + dr
        if 0 <= rr < grid_h:
            if not any(occupied[rr][c] for c in range(start_col, start_col + len(tag))):
                chosen_row = rr
                chosen_col = start_col
                break
    else:
        return

    for idx, ch in enumerate(tag):
        cc = chosen_col + idx
        canvas[chosen_row][cc] = ch
        occupied[chosen_row][cc] = True


def build_screen_grid(
    target_title: str,
    target_box: PixelBox,
    cursor_px: tuple[int, int],
    raw_uia_elements: list[dict],
    profile: AppProfile | None = None,
    grid_w: int = 72,
    grid_h: int = 24,
) -> ScreenGridSnapshot:
    """Build a compact coordinate-indexed ASCII map + element lookup from UIA + AppProfile."""
    canvas = [[" " for _ in range(grid_w)] for _ in range(grid_h)]
    occupied = [[False for _ in range(grid_w)] for _ in range(grid_h)]
    elements: list[UIElement] = []

    # 1. Place normalized AppProfile regions first (draw outlines + [P#] tags)
    if profile is not None:
        for idx, (reg_name, region) in enumerate(profile.regions.items(), start=1):
            pbox = region.to_pixel_box(target_box)
            if pbox.width < 4 or pbox.height < 4:
                continue
            eid = f"P{idx}"
            center = pbox.center
            gcol, grow = pixel_to_grid(center[0], center[1], target_box, grid_w, grid_h)
            elem = UIElement(
                id=eid,
                label=reg_name,
                role=region.role,
                source="profile",
                box=pbox,
                center=center,
                grid_col=gcol,
                grid_row=grow,
            )
            elements.append(elem)
            _draw_region_outline(canvas, pbox, target_box, grid_w, grid_h)
            top_col, top_row = pixel_to_grid(pbox.left + 4, pbox.top + 2, target_box, grid_w, grid_h)
            _place_tag(canvas, occupied, top_col + 3, top_row, _short_tag(eid, reg_name, 16), grid_w, grid_h)

    # 2. Place native Windows UIA controls ([1], [2], ...)
    for idx, item in enumerate(raw_uia_elements, start=1):
        box: PixelBox = item["box"]
        label: str = item.get("label", "control")
        role: str = item.get("role", "control")
        eid = str(idx)
        center = box.center
        gcol, grow = pixel_to_grid(center[0], center[1], target_box, grid_w, grid_h)
        elem = UIElement(
            id=eid,
            label=label,
            role=role,
            source="uia",
            box=box,
            center=center,
            grid_col=gcol,
            grid_row=grow,
        )
        elements.append(elem)
        _place_tag(canvas, occupied, gcol, grow, _short_tag(eid, label), grid_w, grid_h)

    # 3. Overlay current cursor marker `^`
    c_col, c_row = pixel_to_grid(cursor_px[0], cursor_px[1], target_box, grid_w, grid_h)
    canvas[c_row][c_col] = "^"

    # 4. Render framed ASCII text output — compact for minimal token usage
    border_top = "+" + "-" * grid_w + "+"
    hdr = f"{target_title or 'Desktop'} {grid_w}x{grid_h} ^({c_col},{c_row})"
    if profile:
        hdr += f" [{profile.app_id}]"
    lines = [hdr, border_top]
    for row_idx, row_chars in enumerate(canvas):
        lines.append(f"|{''.join(row_chars)}|{row_idx:02d}")
    lines.append(border_top)

    if elements:
        for el in elements[:40]:
            lines.append(
                f"[{el.id}] {el.label} @({el.grid_col},{el.grid_row}) {el.role}"
            )
        if len(elements) > 40:
            lines.append(f"+{len(elements) - 40} more")

    return ScreenGridSnapshot(
        target_title=target_title,
        target_box=target_box,
        cursor_px=cursor_px,
        cursor_grid=(c_col, c_row),
        grid_width=grid_w,
        grid_height=grid_h,
        ascii_map="\n".join(lines),
        elements=elements,
        active_profile=profile.app_id if profile else None,
    )
