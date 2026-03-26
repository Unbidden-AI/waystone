# education_tutoring / calculus_tutoring

Dr. Martinez: How are things going with calculus, Eli? What's feeling most challenging right now?
Eli: Not great, Dr. Martinez. Related rates are really throwing me off. Especially the differentiation part, like when to use `dx/dt` or `dy/dt`.
Dr. Martinez: Ah, that's a classic implicit differentiation hurdle when you're differentiating with respect to time. Let's tackle a common one: the ladder problem. Have you seen it?
Eli: Yeah, a ladder sliding down a wall. I think we did one in class, but I got lost.
Dr. Martinez: Perfect. Imagine a 10-foot ladder leaning against a vertical wall. The base is sliding away from the wall at 2 feet per second. How fast is the top sliding down when the base is 6 feet from the wall? First, draw it and label `x` for the base distance, `y` for the height. What's the primary equation?
Eli: Okay, a right triangle. `x^2 + y^2 = L^2`. So `x^2 + y^2 = 10^2`, which is 100.
Dr. Martinez: Excellent. Now, differentiate that equation with respect to time, `t`. Remember `x` and `y` are functions of `t`.
Eli: So `2x`... and then `dy/dt`? No, wait. `2x dx/dt` plus `2y dy/dt` equals zero, because 100 is a constant.
Dr. Martinez: Fantastic! You caught yourself on the chain rule for `x` and `y`. That's exactly the implicit differentiation we need. Now, what values do we know, and what do we need to find?
Eli: `x` is 6 feet. `dx/dt` is 2 feet per second. We need `dy/dt`. I also need `y`.
Dr. Martinez: How do you find `y` when `x` is 6?
Eli: Using `x^2 + y^2 = 100`. `6^2 + y^2 = 100`. `36 + y^2 = 100`, so `y^2 = 64`, and `y = 8` feet.
Dr. Martinez: Great. Now substitute all those values into your differentiated equation.
Eli: `2(6)(2) + 2(8) dy/dt = 0`. That's `24 + 16 dy/dt = 0`.
Dr. Martinez: Solve for `dy/dt`.
Eli: `16 dy/dt = -24`. So `dy/dt = -24/16`, which is `-3/2` or `-1.5` feet per second.
Dr. Martinez: Perfect! What does that negative sign tell us?
Eli: It means the top of the ladder is sliding *down* the wall.
Dr. Martinez: Exactly. You handled that beautifully. Let's try another. A spherical balloon is being inflated. Air is pumped in at 10 cubic centimeters per second. How fast is the radius increasing when the radius is 5 centimeters? What's the volume formula for a sphere?
Eli: `V = (4/3)πr^3`.
Dr. Martinez: Good. Differentiate that with respect to `t`.
Eli: `dV/dt = (4/3)π * 3r^2 dr/dt`. The `3`s cancel, so `dV/dt = 4πr^2 dr/dt`.
Dr. Martinez: Excellent. Now, plug in your known values.
Eli: `dV/dt` is 10. `r` is 5. So `10 = 4π(5^2) dr/dt`. `10 = 100π dr/dt`.
Dr. Martinez: And `dr/dt`?
Eli: `dr/dt = 10 / (100π) = 1 / (10π)` centimeters per second.
Dr. Martinez: Fantastic, Eli! You've really grasped the implicit differentiation now. For your exam, always start with a diagram, list knowns and unknowns, write the primary equation, differentiate implicitly, then plug in values.
Eli: That makes a lot more sense. The `dx/dt` part was really tripping me up.
Dr. Martinez: Great. For next time, please work through problems 15, 17, and 20 from Chapter 4.1 in your textbook. They cover various related rates scenarios. We'll review them and then move on to optimization.
Eli: Will do, Dr. Martinez. Thanks!
