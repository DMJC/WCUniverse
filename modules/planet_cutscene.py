"""A cinematic shot of a ship landing on, or taking off from, a planet.

A fixed cinematic camera is placed a few planet radii out, on the lit side
of the planet, and looks at it. A stand-in copy of the player's ship is then
flown along a curve:
  landing - from just beside the camera, out across the frame and round the
            planet's limb until it disappears behind it;
  takeoff - the same path in reverse: it rises from behind the planet,
            rounds the limb and sweeps past the camera.
During the shot the player is parked out of sight behind the camera, frozen
and invulnerable, and the HUD is hidden. Afterwards:
  landing - the player is docked at the planet (VS.completePlayerLanding),
            which opens the base screen;
  takeoff - the player is placed where the stand-in left the frame, flying
            away from the planet.

Usage, from a Director.Mission (or one of the privateer loops):
    shot = planet_cutscene.PlanetCutscene(planet, "landing")
    ...each frame:
    if shot.Execute():  # True once it has finished
        shot = None
cutscene_director.py does this automatically for the player's own landings
and takeoffs.

Uses VS.setCinematicCamera / VS.clearCinematicCamera, Unit.SetInvulnerable
and VS.completePlayerLanding from the engine's gfx-overhaul branch.
"""
import VS
import math

# Defaults; each can be overridden per shot with a keyword argument
DEFAULTS = {
    "lead_in": 1.0,          # seconds the camera holds before the ship moves
    "flight_time": 14.0,     # seconds the ship takes along its path
    "hold_after": 2.0,       # seconds the camera holds after the ship is gone
    "camera_distance": 3.2,  # camera distance from planet centre, in planet radii
    "camera_fov": 50.0,      # degrees
    "ship_type": "",         # stand-in ship type; "" = the player's ship
    "dock": True,            # landing: dock the player at the end
    "takeoff_speed": 200.0,  # takeoff: player's speed when control returns
}


# Small vector helpers (VS passes vectors as 3-tuples)
def add(*vs):
    return (sum(v[0] for v in vs), sum(v[1] for v in vs), sum(v[2] for v in vs))


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(v):
    return math.sqrt(dot(v, v))


def norm(v):
    l = length(v)
    if l < 1e-9:
        return (0.0, 0.0, 1.0)
    return mul(v, 1.0 / l)


def bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return add(mul(p0, u * u * u), mul(p1, 3 * u * u * t), mul(p2, 3 * u * t * t), mul(p3, t * t * t))


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def log(msg):
    print("planet_cutscene: " + msg)


def find_sun():
    i = VS.getUnitList()
    while not i.isDone():
        un = i.current()
        i.advance()
        if not un.isNull() and un.isSun():
            return un
    return None


def ship_type_of(player):
    # The unit name is the ship type; some saves leave it as "Dummy" with
    # the type in the full name instead
    name = player.getName()
    if name and name != "Dummy":
        return name
    return player.getFullname()


class PlanetCutscene:
    def __init__(self, planet, mode, **settings):
        self.planet = planet
        self.mode = mode
        self.cfg = dict(DEFAULTS)
        self.cfg.update(settings)
        self.state = "start"
        self.actor = VS.Unit()
        self.actor_end = None
        self.dock_tries = 0

    def done(self):
        return self.state == "done"

    # Everything is stored relative to the planet's centre, so the shot
    # stays correct if the planet moves (orbits) while it plays.
    def setup_shot(self, player):
        R = self.planet.rSize()
        centre = self.planet.Position()

        # Ecliptic "up": systems are laid out in the x-y plane
        world_up = (0.0, 0.0, 1.0)

        # Put the camera on the lit side, swung round so the planet shows a
        # terminator, and a little above the ecliptic
        sun = find_sun()
        to_sun = norm(sub(sun.Position(), centre)) if sun else norm(sub(player.Position(), centre))
        side = norm(cross(world_up, to_sun))
        cam_dir = norm(add(mul(to_sun, 0.55), mul(side, 0.8), mul(world_up, 0.2)))
        self.cam_offset = mul(cam_dir, R * self.cfg["camera_distance"])

        # Camera basis: fwd looks at the planet
        fwd = mul(cam_dir, -1.0)
        right = norm(cross(fwd, world_up))
        up = cross(right, fwd)
        self.up = up
        cam = self.cam_offset

        # Park the player behind the camera, out of the shot
        self.park_offset = mul(cam_dir, R * self.cfg["camera_distance"] * 1.3)

        # Takeoff path, planet-relative (a cubic Bezier): from behind the
        # planet (hidden by it), out round its right-hand limb, then in
        # towards the camera, passing close by its right side to just
        # behind it. Landing plays it backwards: the ship sweeps into frame
        # close up, dwindles towards the planet and slips behind the limb
        # about 75% of the way through. It stays at least 1.18 radii from
        # the planet's centre, and within about 25 degrees of the centre of
        # the frame (data sets with a narrow graphics.aspect crop the sides).
        self.path = (
            add(mul(fwd, R * 0.9), mul(right, R * 0.75), mul(up, R * 0.1)),
            add(mul(fwd, R * 0.2), mul(right, R * 2.05), mul(up, R * 0.25)),
            add(cam, mul(fwd, R * 0.7), mul(right, R * 0.1), mul(up, -R * 0.03)),
            add(cam, mul(fwd, -10.0), mul(right, 22.0), mul(up, -5.0)),
        )

    def path_point(self, t):
        # t runs 0..1 over the shot; landing traverses the path in reverse
        s = smoothstep(t)
        if self.mode == "landing":
            s = 1.0 - s
        return add(self.planet.Position(), bezier(self.path[0], self.path[1], self.path[2], self.path[3], s))

    def place_actor(self, t):
        pos = self.path_point(t)
        ahead = self.path_point(min(1.0, t + 0.01))
        forward = sub(ahead, pos)
        if length(forward) < 1e-6:
            forward = sub(pos, self.path_point(max(0.0, t - 0.01)))
        speed = length(forward) / (0.01 * self.cfg["flight_time"])
        forward = norm(forward)
        q = norm(sub(self.up, mul(forward, dot(self.up, forward))))
        # SetPosAndCumPos moves the drawn position too, not just the physics one
        self.actor.SetPosAndCumPos(pos)
        self.actor.SetOrientation(q, forward)
        self.actor.SetVelocity(mul(forward, speed))
        self.actor.SetAngularVelocity((0.0, 0.0, 0.0))

    def park_player(self, player):
        player.SetPosAndCumPos(add(self.planet.Position(), self.park_offset))
        player.SetVelocity((0.0, 0.0, 0.0))
        player.SetAngularVelocity((0.0, 0.0, 0.0))

    def aim_camera(self):
        # Re-aimed each frame in case the planet moves
        centre = self.planet.Position()
        VS.setCinematicCamera(add(centre, self.cam_offset), centre, self.up, self.cfg["camera_fov"])

    def start(self, player):
        self.setup_shot(player)
        player.SetInvulnerable(True)
        self.park_player(player)

        # The stand-in: same ship type and faction as the player, so nothing
        # treats it as hostile. Its AI is overridden every frame.
        ship = self.cfg["ship_type"] or ship_type_of(player)
        self.actor = VS.launch("Cutscene", ship, player.getFactionName(), "unit", "default",
                               1, 1, self.path_point(0.0), "")
        if not self.actor.isNull():
            self.actor.SetInvulnerable(True)
            self.place_actor(0.0)
        else:
            log("could not launch a stand-in " + ship)

        self.aim_camera()
        self.start_time = VS.GetGameTime()
        self.state = "playing"

    def remove_actor(self):
        if not self.actor.isNull():
            self.actor_end = self.actor.Position()
            self.actor.RemoveFromSystem()
            self.actor = VS.Unit()

    def finish(self, player):
        # A landing keeps the cinematic view up until the dock has gone
        # through, so the cockpit is not seen before the base screen
        if self.mode != "landing" or not self.cfg["dock"]:
            VS.clearCinematicCamera()
        self.remove_actor()
        end_pos = self.actor_end if self.actor_end else self.path_point(1.0)
        player.SetInvulnerable(False)

        if self.mode == "landing":
            # Put the player just above the surface, below where the
            # stand-in disappeared, and dock on a later frame (once the
            # move has taken effect)
            centre = self.planet.Position()
            down = norm(sub(end_pos, centre))
            player.SetPosAndCumPos(add(centre, mul(down, self.planet.rSize() + player.rSize() + 20.0)))
            player.SetVelocity((0.0, 0.0, 0.0))
            if self.cfg["dock"]:
                self.state = "docking"
            else:
                VS.cancelPlayerLanding()
                self.state = "done"
        else:
            # Hand control back where the stand-in left the frame, flying away
            end_dir = norm(sub(self.path_point(1.0), self.path_point(0.99)))
            player.SetPosAndCumPos(end_pos)
            player.SetOrientation(self.up, end_dir)
            player.SetVelocity(mul(end_dir, self.cfg["takeoff_speed"]))
            self.state = "done"

    def dock(self, player):
        player.SetVelocity((0.0, 0.0, 0.0))
        ok = VS.completePlayerLanding(self.planet)
        if ok:
            # The base screen takes over from the next frame
            self.state = "docked"
            return
        self.dock_tries += 1
        if self.dock_tries >= 30:
            log("could not dock at " + self.planet.getName())
            VS.clearCinematicCamera()
            VS.cancelPlayerLanding()
            self.state = "done"

    def abort(self, player):
        """Ends the shot at once, leaving the player where it was parked."""
        if self.state in ("playing", "docking", "docked"):
            VS.clearCinematicCamera()
        if self.state == "playing":
            self.remove_actor()
            if not player.isNull():
                player.SetInvulnerable(False)
        if self.mode == "landing":
            VS.cancelPlayerLanding()
        self.state = "done"

    def Execute(self):
        """Advances the shot by a frame. Returns True once it has finished."""
        player = VS.getPlayer()
        if self.state == "done":
            return True
        if player.isNull() or self.planet.isNull():
            self.abort(player)
            return True

        if self.state == "start":
            self.start(player)
            return False
        if self.state == "docking":
            self.dock(player)
            return self.done()
        if self.state == "docked":
            VS.clearCinematicCamera()
            self.state = "done"
            return True

        # playing
        elapsed = VS.GetGameTime() - self.start_time
        lead_in = self.cfg["lead_in"]
        flight_time = self.cfg["flight_time"]
        self.park_player(player)
        self.aim_camera()
        t = (elapsed - lead_in) / flight_time
        if not self.actor.isNull():
            if t >= 1.0:
                # Gone round the planet (landing) or past the camera (takeoff)
                self.remove_actor()
            else:
                self.place_actor(max(0.0, t))
        if elapsed >= lead_in + flight_time + self.cfg["hold_after"]:
            self.finish(player)
        return self.done()
