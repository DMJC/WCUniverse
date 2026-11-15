"""A cinematic shot of a ship landing on, or taking off from, a planet.

A fixed cinematic camera is placed a few planet radii out, on the lit side
of the planet, and looks at it. A stand-in copy of the player's ship is then
flown along a curve, banking into its turns:
  landing - from just beside the camera, out across the frame and round the
            planet's limb until it disappears behind it;
  takeoff - it rises from behind the planet, rounds its right-hand limb,
            comes in towards the camera down the right of the frame and
            turns left across the front of the planet, ending centred in
            front of it, heading for the left of the screen.
During the shot the player is parked out of sight behind the camera, frozen
and invulnerable, and the HUD is hidden. Esc, Space or Enter skips to the
end of the shot. Afterwards:
  landing - the player is docked at the planet (VS.completePlayerLanding),
            which opens the base screen;
  takeoff - control returns with the player exactly where the stand-in
            ended, flying the way it was heading.

The stand-in is a cutscene actor (Unit.SetCutsceneActor): before each
physics step it is given the position and orientation it reaches at the end
of that step, and drawing interpolates between those, so it moves smoothly
at any frame rate. Its speed along the path is eased by distance travelled,
not by curve parameter, so it does not surge or stall along the curve.

Usage, from a Director.Mission (or one of the privateer loops):
    shot = planet_cutscene.PlanetCutscene(planet, "landing")
    ...each frame:
    if shot.Execute():  # True once it has finished
        shot = None
cutscene_director.py does this automatically for the player's own landings
and takeoffs.

Uses VS.setCinematicCamera / VS.clearCinematicCamera, Unit.SetInvulnerable,
Unit.SetCutsceneActor / SetCutsceneTarget and VS.completePlayerLanding from
the engine's gfx-overhaul branch.
"""
import VS
import math

# Defaults; each can be overridden per shot with a keyword argument
DEFAULTS = {
    "lead_in": 1.0,          # seconds the camera holds before the ship moves
    "flight_time": 14.0,     # seconds the ship takes along its path
    "hold_after": 2.0,       # landing: seconds the camera holds after the ship is gone
    "camera_distance": 3.2,  # camera distance from planet centre, in planet radii
    "camera_fov": 50.0,      # degrees
    "ship_type": "",         # stand-in ship type; "" = the player's ship
    "dock": True,            # landing: dock the player at the end
    "takeoff_end_distance": 6.0,  # takeoff: final distance from the camera, in ship radii
    "takeoff_end_speed": 60.0,    # takeoff: stand-in's speed (m/s) as it reaches the end
    "takeoff_speed": 200.0,  # takeoff: player's speed when control returns
    "max_bank": 35.0,        # degrees the stand-in may roll into a turn
}

PATH_SAMPLES = 200      # samples per curve segment for the distance table
BANK_ACCEL = 40.0       # m/s^2 of turn that gives a 45 degree bank
PHYSICS_STEP = 0.06     # fallback physics step (s) until one has been measured


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


def bezier(seg, t):
    p0, p1, p2, p3 = seg
    u = 1.0 - t
    return add(mul(p0, u * u * u), mul(p1, 3 * u * u * t), mul(p2, 3 * u * t * t), mul(p3, t * t * t))


def bezier_tangent(seg, t):
    p0, p1, p2, p3 = seg
    u = 1.0 - t
    return add(mul(sub(p1, p0), 3 * u * u), mul(sub(p2, p1), 6 * u * t), mul(sub(p3, p2), 3 * t * t))


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def ease_out_to(t, end_rate):
    """Eases from rest at t=0 to 1 at t=1, arriving with slope end_rate."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t) + end_rate * (t * t * t - t * t)


def ease_out_to_slope(t, end_rate):
    t = max(0.0, min(1.0, t))
    return 6.0 * t * (1.0 - t) + end_rate * (3.0 * t * t - 2.0 * t)


def log(msg):
    print("planet_cutscene: " + msg)


class Path:
    """A chain of cubic Bezier segments, looked up by distance travelled."""

    def __init__(self, segments):
        self.segments = segments
        self.table = [(0.0, 0, 0.0)]  # (distance, segment, parameter)
        total = 0.0
        for i, seg in enumerate(segments):
            last = bezier(seg, 0.0)
            for k in range(1, PATH_SAMPLES + 1):
                u = k / float(PATH_SAMPLES)
                pt = bezier(seg, u)
                total += length(sub(pt, last))
                self.table.append((total, i, u))
                last = pt
        self.length = total

    def locate(self, s):
        """Segment and curve parameter at fraction s (0..1) of the length."""
        d = max(0.0, min(1.0, s)) * self.length
        lo, hi = 0, len(self.table) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.table[mid][0] > d:
                hi = mid
            else:
                lo = mid
        d0, i0, u0 = self.table[lo]
        d1, i1, u1 = self.table[hi]
        if i0 != i1:
            i0, u0 = i1, 0.0
        f = 0.0 if d1 == d0 else (d - d0) / (d1 - d0)
        return i1, u0 + (u1 - u0) * f

    def point(self, s):
        i, u = self.locate(s)
        return bezier(self.segments[i], u)

    def direction(self, s):
        i, u = self.locate(s)
        return norm(bezier_tangent(self.segments[i], u))


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
        self.last_time = None
        self.step = PHYSICS_STEP

    def done(self):
        return self.state == "done"

    # Everything is stored relative to the planet's centre, so the shot
    # stays correct if the planet moves (orbits) while it plays.
    def setup_shot(self, player, ship_radius):
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

        def planet_pt(f, r, u):
            # planet-relative: f towards the far side, r right, u up
            return add(mul(fwd, f), mul(right, r), mul(up, u))

        def camera_pt(r, u, depth):
            # camera-relative: depth in front of the camera
            return add(cam, mul(right, r), mul(up, u), mul(fwd, depth))

        behind = planet_pt(R * 0.9, R * 0.75, R * 0.1)  # hidden behind the right-hand limb
        if self.mode == "landing":
            # Sweeps into frame close up beside the camera, dwindles towards
            # the planet and slips behind its right-hand limb about 75% of
            # the way through; at least 1.18 radii from the centre, and
            # within about 25 degrees of the centre of the frame
            self.path = Path([(
                camera_pt(22.0, -5.0, -10.0),
                camera_pt(R * 0.1, -R * 0.03, R * 0.7),
                planet_pt(R * 0.2, R * 2.05, R * 0.25),
                behind,
            )])
            self.end_rate = 0.0
        else:
            # Out round the right-hand limb to a waypoint down the right of
            # the frame, then in and across to the left, ending centred in
            # front of the planet heading left. Tuned to keep at least 1.15
            # radii from the centre and within about 25 degrees of the
            # centre of the frame (graphics.aspect 1.3 crops the sides)
            depth = R * 0.8
            waypoint = camera_pt(depth * math.tan(math.radians(23.0)), R * 0.04, depth)
            heading = add(mul(right, math.sin(math.radians(-150.0))), mul(fwd, math.cos(math.radians(-150.0))))
            end = camera_pt(0.0, 0.0, ship_radius * self.cfg["takeoff_end_distance"])
            self.path = Path([
                (behind,
                 add(behind, mul(norm(sub(mul(right, 0.6), mul(fwd, 0.2))), R)),
                 sub(waypoint, mul(heading, R)),
                 waypoint),
                (waypoint,
                 add(waypoint, mul(heading, R * 0.3)),
                 add(end, mul(right, R * 0.2)),
                 end),
            ])
            # Arrive at takeoff_end_speed rather than stopping
            average = self.path.length / self.cfg["flight_time"]
            self.end_rate = self.cfg["takeoff_end_speed"] / average if average > 0 else 0.0
            self.end_rate = min(self.end_rate, 2.5)  # beyond 3 the easing would overshoot

    def progress(self, t):
        """Fraction of the path covered at time fraction t, and its rate."""
        if self.end_rate > 0:
            return ease_out_to(t, self.end_rate), ease_out_to_slope(t, self.end_rate)
        tc = max(0.0, min(1.0, t))
        return smoothstep(t), 6.0 * tc * (1.0 - tc)

    def pose(self, t):
        """World position, up, forward and speed of the stand-in at time fraction t."""
        s, rate = self.progress(t)
        centre = self.planet.Position()
        pos = add(centre, self.path.point(s))
        forward = self.path.direction(s)
        speed = rate * self.path.length / self.cfg["flight_time"]

        # Level relative to the camera's up, then bank into the turn
        up = norm(sub(self.up, mul(forward, dot(self.up, forward))))
        ds = 2.0 / max(self.path.length, 1.0)
        bend = sub(self.path.direction(min(1.0, s + ds)), self.path.direction(max(0.0, s - ds)))
        span = (min(1.0, s + ds) - max(0.0, s - ds)) * self.path.length
        if span > 0:
            curvature = mul(bend, 1.0 / span)  # points towards the inside of the turn
            side = sub(curvature, add(mul(forward, dot(curvature, forward)), mul(up, dot(curvature, up))))
            turn = length(side) * speed * speed
            if turn > 1e-6:
                bank = min(math.radians(self.cfg["max_bank"]), math.atan(turn / BANK_ACCEL))
                up = norm(add(mul(up, math.cos(bank)), mul(norm(side), math.sin(bank))))
        return pos, up, forward, speed

    def drive_actor(self, t):
        # Called before the physics step: give the state at the end of it
        pos, up, forward, speed = self.pose(t)
        self.actor.SetCutsceneTarget(pos, up, forward)
        self.actor.SetVelocity(mul(forward, speed))

    def park_player(self, player):
        player.SetPosAndCumPos(add(self.planet.Position(), self.park_offset))
        player.SetVelocity((0.0, 0.0, 0.0))
        player.SetAngularVelocity((0.0, 0.0, 0.0))

    def aim_camera(self):
        # Re-aimed each frame in case the planet moves
        centre = self.planet.Position()
        VS.setCinematicCamera(add(centre, self.cam_offset), centre, self.up, self.cfg["camera_fov"])

    def start(self, player):
        player.SetInvulnerable(True)

        # The stand-in: same ship type and faction as the player, so nothing
        # treats it as hostile. Placed out of the way until the path is known
        ship = self.cfg["ship_type"] or ship_type_of(player)
        self.actor = VS.launch("Cutscene", ship, player.getFactionName(), "unit", "default",
                               1, 1, add(player.Position(), (0.0, 0.0, 200.0)), "")
        if self.actor.isNull():
            log("could not launch a stand-in " + ship)
            ship_radius = player.rSize()
        else:
            ship_radius = self.actor.rSize()
            self.actor.SetInvulnerable(True)
            self.actor.PrimeOrders()  # no AI: it would steer and shoot
            self.actor.SetCutsceneActor(True)

        self.setup_shot(player, ship_radius)
        self.park_player(player)
        if not self.actor.isNull():
            pos, up, forward, speed = self.pose(0.0)
            self.actor.SetPosAndCumPos(pos)
            self.actor.SetOrientation(up, forward)
            self.drive_actor(0.0)

        self.aim_camera()
        self.start_time = VS.GetGameTime()
        self.state = "playing"

    def remove_actor(self):
        if not self.actor.isNull():
            self.actor_end = (self.actor.Position(),) + self.pose(1.0)[1:3]
            self.actor.RemoveFromSystem()
            self.actor = VS.Unit()

    def finish(self, player):
        # A landing keeps the cinematic view up until the dock has gone
        # through, so the cockpit is not seen before the base screen
        if self.mode != "landing" or not self.cfg["dock"]:
            VS.clearCinematicCamera()
        self.remove_actor()
        player.SetInvulnerable(False)

        if self.mode == "landing":
            # Put the player just above the surface, below where the
            # stand-in disappeared, and dock on a later frame (once the
            # move has taken effect)
            end_pos = self.actor_end[0] if self.actor_end else add(self.planet.Position(), self.path.point(1.0))
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
            # Hand control back exactly where the stand-in ended, flying on
            # the way it was heading
            pos, up, forward, speed = self.pose(1.0)
            player.SetPosAndCumPos(pos)
            player.SetOrientation(up, forward)
            player.SetVelocity(mul(forward, self.cfg["takeoff_speed"]))
            # and the flight computer's set speed, or it brakes straight away
            player.setSpeed(self.cfg["takeoff_speed"])
            self.state = "done"

    def dock(self, player):
        player.SetVelocity((0.0, 0.0, 0.0))
        if VS.completePlayerLanding(self.planet):
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

        now = VS.GetGameTime()
        if self.last_time is not None and now > self.last_time:
            self.step = now - self.last_time
        self.last_time = now

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
        if VS.takeCinematicSkip():
            # Esc, Space or Enter: jump to the end of the shot (landing: dock,
            # takeoff: hand control back where the stand-in would have ended)
            self.finish(player)
            return self.done()
        elapsed = now - self.start_time
        lead_in = self.cfg["lead_in"]
        flight_time = self.cfg["flight_time"]
        self.park_player(player)
        self.aim_camera()
        # This runs before the physics step, so aim for the end of the step
        t_next = (elapsed + self.step - lead_in) / flight_time
        if self.mode == "takeoff":
            # Cut back to the cockpit as the stand-in arrives centre frame
            if t_next >= 1.0:
                self.finish(player)
                return self.done()
            if not self.actor.isNull():
                self.drive_actor(max(0.0, t_next))
            return False

        if not self.actor.isNull():
            if t_next >= 1.0:
                # Gone round the planet
                self.remove_actor()
            else:
                self.drive_actor(max(0.0, t_next))
        if elapsed >= lead_in + flight_time + self.cfg["hold_after"]:
            self.finish(player)
        return self.done()
