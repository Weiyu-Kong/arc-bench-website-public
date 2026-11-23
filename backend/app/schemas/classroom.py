from pydantic import BaseModel, Field


class ClassroomSummary(BaseModel):
    id: str
    name: str
    description: str
    member_count: int
    team_count: int


class ClassroomTeamMember(BaseModel):
    user_id: str
    name: str
    is_leader: bool


class ClassroomTeam(BaseModel):
    id: str
    name: str
    leader_user_id: str
    members: list[ClassroomTeamMember] = Field(default_factory=list)


class ClassroomTrackSummary(BaseModel):
    id: str
    title: str
    status: str


class MyClassroomResponse(BaseModel):
    classroom: ClassroomSummary | None = None
    team: ClassroomTeam | None = None
    tracks: list[ClassroomTrackSummary] = Field(default_factory=list)
    teams: list[ClassroomTeam] = Field(default_factory=list)
    is_team_leader: bool = False
