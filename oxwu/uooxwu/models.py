from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Town:
    town_id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None

    @classmethod
    def from_api(cls, town_id: str, payload: dict[str, Any]) -> "Town":
        return cls(
            town_id=town_id,
            name=payload.get("name", town_id),
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
        )


@dataclass(slots=True)
class WarningLocation:
    latitude: str | None = None
    longitude: str | None = None
    text: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None) -> "WarningLocation | None":
        if not payload:
            return None
        return cls(
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            text=payload.get("text"),
        )


@dataclass(slots=True)
class WarningRecord:
    id: str | None = None
    time: str | None = None
    latlon: str | None = None
    depth: str | None = None
    mag: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "WarningRecord":
        return cls(
            id=payload.get("id"),
            time=payload.get("time"),
            latlon=payload.get("latlon"),
            depth=payload.get("depth"),
            mag=payload.get("mag"),
        )


@dataclass(slots=True)
class Warning:
    ok: bool = False
    time: str | None = None
    location: WarningLocation | None = None
    depth: str | None = None
    magnitude: str | None = None
    max_intensity: str | None = None
    intensity: str | None = None
    eta: str | None = None
    list: list[WarningRecord] = field(default_factory=list)
    url: str | None = None
    arrival_times: dict[str, int] = field(default_factory=dict)
    estimated_intensities: dict[str, str] = field(default_factory=dict)
    arrival_count: int = 0
    arrival_generated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Warning":
        return cls(
            ok=payload.get("ok", False),
            time=payload.get("time"),
            location=WarningLocation.from_api(payload.get("location")),
            depth=payload.get("depth"),
            magnitude=payload.get("magnitude"),
            max_intensity=payload.get("maxIntensity"),
            intensity=payload.get("intensity"),
            eta=payload.get("eta"),
            list=[WarningRecord.from_api(item) for item in payload.get("list", [])],
            url=payload.get("url"),
            arrival_times={str(k): int(v) for k, v in payload.get("arrival_times", {}).items()},
            estimated_intensities={
                str(k): str(v) for k, v in payload.get("estimated_intensities", {}).items()
            },
            arrival_count=int(payload.get("arrival_count", 0) or 0),
            arrival_generated_at=payload.get("arrival_generated_at"),
            raw=payload,
        )


@dataclass(slots=True)
class ReportStationGroup:
    level: str | None = None
    names: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ReportStationGroup":
        return cls(
            level=payload.get("level"),
            names=list(payload.get("names", [])),
        )


@dataclass(slots=True)
class ReportIntensityArea:
    area: str | None = None
    max_intensity: str | None = None
    stations: list[ReportStationGroup] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ReportIntensityArea":
        return cls(
            area=payload.get("area"),
            max_intensity=payload.get("maxIntensity"),
            stations=[ReportStationGroup.from_api(item) for item in payload.get("stations", [])],
        )


@dataclass(slots=True)
class ReportData:
    number: str | None = None
    time: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    depth: str | None = None
    magnitude: str | None = None
    max_intensity: str | None = None
    intensities: list[ReportIntensityArea] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ReportData":
        return cls(
            number=payload.get("number"),
            time=payload.get("time"),
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            depth=payload.get("depth"),
            magnitude=payload.get("magnitude"),
            max_intensity=payload.get("maxIntensity"),
            intensities=[ReportIntensityArea.from_api(item) for item in payload.get("intensities", [])],
        )


@dataclass(slots=True)
class Report:
    ok: bool = False
    report: ReportData | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Report":
        return cls(
            ok=payload.get("ok", False),
            report=ReportData.from_api(payload.get("report", {})),
            raw=payload,
        )


@dataclass(slots=True)
class EventTimeParts:
    year: str | None = None
    month: str | None = None
    date: str | None = None
    hour: str | None = None
    minute: str | None = None
    second: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None) -> "EventTimeParts | None":
        if not payload:
            return None
        return cls(
            year=payload.get("year"),
            month=payload.get("month"),
            date=payload.get("date"),
            hour=payload.get("hour"),
            minute=payload.get("minute"),
            second=payload.get("second"),
        )


@dataclass(slots=True)
class WarningUpdateEvent:
    time: str | None = None
    parts: EventTimeParts | None = None
    url: str | None = None
    warning: Warning | None = None
    arrival_times: dict[str, int] = field(default_factory=dict)
    estimated_intensities: dict[str, str] = field(default_factory=dict)
    arrival_count: int = 0
    arrival_generated_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "WarningUpdateEvent":
        warning_payload = payload.get("data") or {}
        arrival_times = payload.get("arrival_times")
        if arrival_times is None:
            arrival_times = warning_payload.get("arrival_times", {})
        estimated_intensities = payload.get("estimated_intensities")
        if estimated_intensities is None:
            estimated_intensities = warning_payload.get("estimated_intensities", {})
        return cls(
            time=payload.get("time"),
            parts=EventTimeParts.from_api(payload.get("parts")),
            url=payload.get("url"),
            warning=Warning.from_api(warning_payload) if warning_payload else None,
            arrival_times={str(k): int(v) for k, v in arrival_times.items()},
            estimated_intensities={str(k): str(v) for k, v in estimated_intensities.items()},
            arrival_count=int(payload.get("arrival_count", warning_payload.get("arrival_count", 0)) or 0),
            arrival_generated_at=payload.get(
                "arrival_generated_at",
                warning_payload.get("arrival_generated_at"),
            ),
            raw=payload,
        )


@dataclass(slots=True)
class ReportUpdateEvent:
    time: str | None = None
    parts: EventTimeParts | None = None
    url: str | None = None
    report: Report | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ReportUpdateEvent":
        report_payload = payload.get("data") or {}
        return cls(
            time=payload.get("time"),
            parts=EventTimeParts.from_api(payload.get("parts")),
            url=payload.get("url"),
            report=Report.from_api(report_payload) if report_payload else None,
            raw=payload,
        )
