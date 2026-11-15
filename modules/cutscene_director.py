"""Plays planet_cutscene shots when the player lands on or takes off from a
planet.

Add an instance to a Director.Mission that runs every frame (privateer.py
puts one in its loops) and call its Execute() each frame. It turns on the
engine's docking cutscene hooks:
  - a dock with a planet is deferred by the engine; the director plays the
    landing shot and then completes the dock, which opens the base screen;
  - launching from a planet's base screen is reported; the director plays
    the takeoff shot.
Docking with stations and ships is left alone.

A shot is skipped (landing docks straight away) when a hostile ship is
within HOSTILE_RANGE of the player, so a cutscene never hides a fight.
"""
import VS
import planet_cutscene

ENABLED = True
HOSTILE_RANGE = 20000.0  # metres; 0 never skips a shot
SHOT_SETTINGS = {}       # overrides for planet_cutscene.DEFAULTS


def hostile_near(player, radius):
    if radius <= 0:
        return False
    i = VS.getUnitList()
    while not i.isDone():
        un = i.current()
        i.advance()
        if un.isNull() or un == player or un.isPlanet() or un.isSun() or un.isJumppoint():
            continue
        if un.getRelation(player) < 0 and player.getDistance(un) < radius:
            return True
    return False


class cutscene_director:
    def __init__(self):
        self.shot = None
        self.enabled = False
        self.busy = False

    def Execute(self):
        # Completing a landing opens the base screen, which runs the mission
        # scripts again (and the whole base visit) from inside that call:
        # ignore those nested calls and carry on when it returns
        if self.busy:
            return
        self.busy = True
        try:
            self.step()
        finally:
            self.busy = False

    def step(self):
        if not self.enabled:
            # Turned on from here rather than at import, so it is only on
            # while something is running the director
            VS.setDockingCutscenes(ENABLED)
            self.enabled = True
            if not ENABLED:
                return

        if self.shot is not None:
            if not self.shot.Execute():
                return
            self.shot = None

        player = VS.getPlayer()
        landing = VS.takePendingLanding()
        takeoff = VS.takePendingTakeoff()
        if player.isNull():
            if not landing.isNull():
                VS.cancelPlayerLanding()
            return

        if not landing.isNull():
            if hostile_near(player, HOSTILE_RANGE):
                if not VS.completePlayerLanding(landing):
                    VS.cancelPlayerLanding()
                return
            self.shot = planet_cutscene.PlanetCutscene(landing, "landing", **SHOT_SETTINGS)
            self.shot.Execute()
        elif not takeoff.isNull() and takeoff.isPlanet() and not takeoff.isJumppoint():
            if hostile_near(player, HOSTILE_RANGE):
                # A landing shot's view can still be up if the script did
                # not run while docked
                VS.clearCinematicCamera()
                return
            self.shot = planet_cutscene.PlanetCutscene(takeoff, "takeoff", **SHOT_SETTINGS)
            self.shot.Execute()
