// Temporary virtual displays on macOS, for testing multi-monitor capture on a
// Mac with one screen. macOS treats them as real monitors (ScreenCaptureKit
// lists and captures them); they vanish when this program exits.
//
//   vdisplay 1920x1080 1280x1024 1080x1920     prints one id per line, then waits
//   stdin "drop <n>"  removes the n-th display (1-based): a monitor unplugged
//   stdin EOF or "quit"  removes them all
//
// Uses CoreGraphics' CGVirtualDisplay (private API; the same that display tools
// use). Test tool only - never shipped.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>

@interface CGVirtualDisplayDescriptor : NSObject
@property (retain, nonatomic) dispatch_queue_t queue;
@property (retain, nonatomic) NSString *name;
@property (nonatomic) unsigned int maxPixelsWide, maxPixelsHigh;
@property (nonatomic) CGSize sizeInMillimeters;
@property (nonatomic) unsigned int productID, vendorID, serialNum;
@property (copy, nonatomic) void (^terminationHandler)(id, id);
@end
@interface CGVirtualDisplayMode : NSObject
- (instancetype)initWithWidth:(unsigned int)w height:(unsigned int)h refreshRate:(double)r;
@end
@interface CGVirtualDisplaySettings : NSObject
@property (retain, nonatomic) NSArray *modes;
@property (nonatomic) unsigned int hiDPI;
@end
@interface CGVirtualDisplay : NSObject
- (instancetype)initWithDescriptor:(CGVirtualDisplayDescriptor *)d;
- (BOOL)applySettings:(CGVirtualDisplaySettings *)s;
@property (readonly, nonatomic) unsigned int displayID;
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        NSMutableArray *displays = [NSMutableArray array];
        for (int i = 1; i < argc; i++) {
            unsigned w = 0, h = 0;
            if (sscanf(argv[i], "%ux%u", &w, &h) != 2) { fprintf(stderr, "bad size %s\n", argv[i]); return 2; }
            CGVirtualDisplayDescriptor *d = [CGVirtualDisplayDescriptor new];
            d.queue = dispatch_get_main_queue();
            d.name = [NSString stringWithFormat:@"HID-Fi test %d", i];
            d.maxPixelsWide = w; d.maxPixelsHigh = h;
            d.sizeInMillimeters = CGSizeMake(w * 0.25, h * 0.25);
            d.productID = 0x4846 + i; d.vendorID = 0x4846; d.serialNum = i;
            d.terminationHandler = ^(id a, id b) {};
            CGVirtualDisplay *v = [[CGVirtualDisplay alloc] initWithDescriptor:d];
            if (!v) { fprintf(stderr, "could not create a virtual display\n"); return 3; }
            CGVirtualDisplaySettings *s = [CGVirtualDisplaySettings new];
            s.hiDPI = 0;
            s.modes = @[[[CGVirtualDisplayMode alloc] initWithWidth:w height:h refreshRate:60]];
            if (![v applySettings:s]) { fprintf(stderr, "could not set its mode\n"); return 4; }
            [displays addObject:v];
            printf("%u %ux%u\n", v.displayID, w, h);
        }
        fflush(stdout);
        dispatch_async(dispatch_get_global_queue(0, 0), ^{
            char line[64];
            while (fgets(line, sizeof line, stdin)) {
                int n = 0;
                if (sscanf(line, "drop %d", &n) == 1 && n >= 1 && n <= (int)displays.count) {
                    dispatch_sync(dispatch_get_main_queue(), ^{
                        displays[n - 1] = [NSNull null];   // released: the monitor is gone
                    });
                    printf("dropped %d\n", n); fflush(stdout);
                } else if (strncmp(line, "quit", 4) == 0) break;
            }
            exit(0);
        });
        // Hard stop after 5 minutes, whatever happens: never leave displays behind.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 300 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ exit(0); });
        dispatch_main();
    }
}
