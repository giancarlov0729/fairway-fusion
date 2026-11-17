// Golf sensing dual-camera enclosure prototype
// Units: mm. Parametric OpenSCAD source.
// Select a part by setting part = "enclosure", "rear_cover", "camera_rail",
// "camera_carriage", "pico_plate", or "assembly".

$fn = 48;
part = "assembly";

// Main enclosure dimensions
box_w = 250;
box_h = 180;
box_d = 120;
wall = 3;
clearance = 0.35;

// Camera / mounting assumptions
camera_board = 38;       // Arducam IMX296 Raspberry Pi-style PCB version
rail_h = 145;
rail_w = 20;
rail_t = 6;
carriage_w = 64;
carriage_h = 64;
carriage_t = 5;

// Pico board approximation: Raspberry Pi Pico 51 x 21 mm
pico_l = 51;
pico_w = 21;

module rounded_box(x,y,z,r=2) {
    minkowski() {
        cube([x-2*r,y-2*r,z-2*r], center=true);
        sphere(r=r);
    }
}

module screw_hole(d=4.3,h=20) { cylinder(d=d,h=h,center=true); }

module enclosure() {
    // Open front (y=0), closed rear, sides, top and bottom.
    difference() {
        translate([0,box_d/2,0]) cube([box_w,box_d,box_h],center=true);
        translate([0,(box_d-wall)/2,0])
            cube([box_w-2*wall,box_d-wall+0.2,box_h-2*wall],center=true);
        // Horizontal ventilation / cable relief slots in rear wall
        for (x=[-65,65])
            translate([x,box_d-wall/2, -box_h/2+24])
                cube([28,wall+2,8],center=true);
        // Rear cover screw holes, four corners
        for (x=[-box_w/2+12,box_w/2-12], z=[-box_h/2+12,box_h/2-12])
            translate([x,box_d-wall/2,z]) rotate([90,0,0]) cylinder(d=3.4,h=wall+3,center=true);
    }
    // Internal rear-wall bosses for camera rails, at two horizontal positions.
    for (x=[-55,55])
        for (z=[-rail_h/2+10, rail_h/2-10])
            translate([x,box_d-wall-3,z]) difference() {
                rotate([90,0,0]) cylinder(d=10,h=10,center=true);
                rotate([90,0,0]) cylinder(d=3.2,h=12,center=true);
            }
    // Pico plate mounting bosses aligned to two plates centered at x=+/-55, z=55
    for (x=[-85,-25,25,85], z=[40,70])
        translate([x,box_d-wall-2,z]) difference() {
            rotate([90,0,0]) cylinder(d=8,h=8,center=true);
            rotate([90,0,0]) cylinder(d=3.2,h=10,center=true);
        }
}

module rear_cover() {
    // Cover sits against the rear exterior. Four M3 clearance holes.
    difference() {
        translate([0,box_d+1.5,0]) cube([box_w-1,3,box_h-1],center=true);
        for (x=[-box_w/2+12,box_w/2-12], z=[-box_h/2+12,box_h/2-12])
            translate([x,box_d+1.5,z]) rotate([90,0,0]) cylinder(d=3.4,h=8,center=true);
    }
}

module camera_rail() {
    // Vertical rail; carriage screws travel in two long vertical slots.
    // Horizontal slots at the ends allow the entire rail to shift left/right.
    difference() {
        translate([0,0,rail_h/2]) cube([rail_w,rail_t,rail_h],center=true);
        for (z=[rail_h/2-27,rail_h/2+27])
            hull() {
                translate([0,0,z-9]) rotate([90,0,0]) cylinder(d=4.5,h=rail_t+2,center=true);
                translate([0,0,z+9]) rotate([90,0,0]) cylinder(d=4.5,h=rail_t+2,center=true);
            }
        for (z=[10,rail_h-10])
            hull() {
                translate([-10,0,z]) rotate([90,0,0]) cylinder(d=3.6,h=rail_t+2,center=true);
                translate([10,0,z]) rotate([90,0,0]) cylinder(d=3.6,h=rail_t+2,center=true);
            }
    }
}

module camera_carriage() {
    // Vertical XZ plate, with two M4 rail clamp holes and four universal
    // camera PCB slots. Camera lens faces toward the open front of enclosure.
    difference() {
        cube([carriage_w,carriage_t,carriage_h],center=true);
        // Rail clamp holes, vertically separated 54 mm
        for (z=[-27,27])
            translate([0,0,z]) rotate([90,0,0]) cylinder(d=4.5,h=carriage_t+2,center=true);
        // Camera PCB slots; adjustable around a nominal 31 mm square pattern
        for (x=[-15.5,15.5], z=[-15.5,15.5])
            hull() {
                translate([x-2.5,0,z]) rotate([90,0,0]) cylinder(d=3.2,h=carriage_t+2,center=true);
                translate([x+2.5,0,z]) rotate([90,0,0]) cylinder(d=3.2,h=carriage_t+2,center=true);
            }
        // FFC / connector clearance
        cube([18,carriage_t+2,10],center=true);
    }
    // PCB stand-off pads on the camera-facing surface
    for (x=[-15.5,15.5], z=[-15.5,15.5])
        translate([x,-carriage_t/2-0.6,z]) rotate([90,0,0]) cylinder(d=6,h=1.2,center=true);
}

module pico_plate() {
    // Universal Pico mounting plate with approximate Pico hole pattern.
    difference() {
        cube([72,42,4],center=true);
        // Pico mounting holes: approx 48.26 x 17.78 mm
        for (x=[-24.13,24.13], y=[-8.89,8.89])
            translate([x,y,0]) cylinder(d=2.6,h=8,center=true);
        // Plate-to-enclosure mounting holes
        for (x=[-30,30], y=[-15,15])
            translate([x,y,0]) cylinder(d=3.4,h=8,center=true);
        // Cable tie slots
        for (x=[-12,12])
            translate([x,0,0]) cube([3,18,8],center=true);
    }
}

module assembly_preview() {
    color("#b8c1cc") enclosure();
    // Rails mounted on inside rear wall; cameras toward open front.
    for (x=[-55,55]) {
        translate([x,box_d-wall-10,-rail_h/2]) color("#667788") camera_rail();
        translate([x,box_d-wall-14,20]) color("#d49a55") camera_carriage();
    }
    // Pico plates placed behind the camera plane, lower/upper rear zones.
    for (x=[-55,55])
        translate([x,box_d-wall-3,55]) rotate([90,0,0]) color("#5c9d78") pico_plate();
    color("#8d99a6",0.55) rear_cover();
}

if (part == "enclosure") enclosure();
else if (part == "rear_cover") rear_cover();
else if (part == "camera_rail") camera_rail();
else if (part == "camera_carriage") camera_carriage();
else if (part == "pico_plate") pico_plate();
else assembly_preview();
