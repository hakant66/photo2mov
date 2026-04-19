#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreImage/CoreImage.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>

static const int32_t kFramesPerSecond = 60;
static const double kDefaultDurationSeconds = 5.0;
static const CGFloat kDefaultStartScale = 1.0;
static const CGFloat kDefaultEndScale = 1.11;
static const float kHighlightExposure = 0.3f;
static const float kHighlightContrast = 1.12f;
static const float kHighlightSharpness = 0.45f;
static const NSInteger kTargetBitrate = 20000000;

static NSString *ExpandPath(NSString *rawPath) {
    return [[rawPath stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]] stringByExpandingTildeInPath];
}

static CGFloat SmoothStep(CGFloat t) {
    return t * t * (3.0 - (2.0 * t));
}

static CIImage *WarmTone(CIImage *image) {
    CIImage *warmed = [image imageByApplyingFilter:@"CIColorMatrix"
                               withInputParameters:@{
                                   @"inputRVector": [CIVector vectorWithX:1.03 Y:0 Z:0 W:0],
                                   @"inputGVector": [CIVector vectorWithX:0 Y:1.0 Z:0 W:0],
                                   @"inputBVector": [CIVector vectorWithX:0 Y:0 Z:0.98 W:0],
                                   @"inputAVector": [CIVector vectorWithX:0 Y:0 Z:0 W:1]
                               }];

    return [warmed imageByApplyingFilter:@"CIColorControls"
                     withInputParameters:@{
                         @"inputSaturation": @1.02,
                         @"inputContrast": @1.02
                     }];
}

static CIImage *FocusTreatment(CIImage *image) {
    CIImage *exposed = [image imageByApplyingFilter:@"CIExposureAdjust"
                                withInputParameters:@{@"inputEV": @(kHighlightExposure)}];
    CIImage *contrasted = [exposed imageByApplyingFilter:@"CIColorControls"
                                     withInputParameters:@{@"inputContrast": @(kHighlightContrast)}];
    return [contrasted imageByApplyingFilter:@"CISharpenLuminance"
                         withInputParameters:@{@"inputSharpness": @(kHighlightSharpness)}];
}

static CIImage *CenterMask(CGRect rect) {
    CGFloat minSide = MIN(rect.size.width, rect.size.height);
    CGFloat radius0 = minSide * 0.18;
    CGFloat radius1 = minSide * 0.52;

    CIFilter *gradient = [CIFilter filterWithName:@"CIRadialGradient"];
    [gradient setValuesForKeysWithDictionary:@{
        @"inputCenter": [CIVector vectorWithX:CGRectGetMidX(rect) Y:CGRectGetMidY(rect)],
        @"inputRadius0": @(radius0),
        @"inputRadius1": @(radius1),
        @"inputColor0": [CIColor colorWithRed:1 green:1 blue:1 alpha:1],
        @"inputColor1": [CIColor colorWithRed:0 green:0 blue:0 alpha:0]
    }];

    return [[gradient outputImage] imageByCroppingToRect:rect];
}

static CGAffineTransform FrameTransform(CGFloat scale, CGRect rect) {
    CGAffineTransform transform = CGAffineTransformIdentity;
    transform = CGAffineTransformTranslate(transform, CGRectGetMidX(rect), CGRectGetMidY(rect));
    transform = CGAffineTransformScale(transform, scale, scale);
    transform = CGAffineTransformTranslate(transform, -CGRectGetMidX(rect), -CGRectGetMidY(rect));
    return transform;
}

static CIImage *MakeFrame(CIImage *baseImage, CGRect rect, CIImage *mask, CGFloat scale) {
    CIImage *zoomed = [[baseImage imageByApplyingTransform:FrameTransform(scale, rect)] imageByCroppingToRect:rect];
    CIImage *baseLook = WarmTone(zoomed);
    CIImage *focused = FocusTreatment(baseLook);

    CIImage *blended = [focused imageByApplyingFilter:@"CIBlendWithMask"
                                  withInputParameters:@{
                                      @"inputMaskImage": mask,
                                      @"inputBackgroundImage": baseLook
                                  }];

    return [blended imageByCroppingToRect:rect];
}

static NSURL *OutputURLForInputURL(NSURL *inputURL) {
    NSURL *withoutExtension = [inputURL URLByDeletingPathExtension];
    return [withoutExtension URLByAppendingPathExtension:@"mov"];
}

static AVVideoCodecType SelectCodec(AVAssetWriter *writer, NSInteger width, NSInteger height) {
    NSArray<AVVideoCodecType> *candidates = @[
        AVVideoCodecTypeH264
    ];

    for (AVVideoCodecType codec in candidates) {
        NSDictionary *settings = @{
            AVVideoCodecKey: codec,
            AVVideoWidthKey: @(width),
            AVVideoHeightKey: @(height)
        };
        if ([writer canApplyOutputSettings:settings forMediaType:AVMediaTypeVideo]) {
            return codec;
        }
    }

    return nil;
}

static BOOL LoadImageAtURL(NSURL *url, CIImage **outImage, CGRect *outRect, NSError **outError) {
    CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (source == NULL) {
        if (outError != NULL) {
            *outError = [NSError errorWithDomain:@"PhotoToMov" code:10 userInfo:@{NSLocalizedDescriptionKey: @"Unable to open the input image."}];
        }
        return NO;
    }

    CGImageRef cgImage = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    if (cgImage == NULL) {
        CFRelease(source);
        if (outError != NULL) {
            *outError = [NSError errorWithDomain:@"PhotoToMov" code:11 userInfo:@{NSLocalizedDescriptionKey: @"Unable to decode the input image."}];
        }
        return NO;
    }

    NSDictionary *properties = CFBridgingRelease(CGImageSourceCopyPropertiesAtIndex(source, 0, NULL));
    NSNumber *orientationNumber = properties[(NSString *)kCGImagePropertyOrientation];

    CIImage *image = [[CIImage alloc] initWithCGImage:cgImage];
    if (orientationNumber != nil) {
        image = [image imageByApplyingOrientation:orientationNumber.intValue];
    }

    CGRect extent = CGRectIntegral(image.extent);
    image = [image imageByApplyingTransform:CGAffineTransformMakeTranslation(-extent.origin.x, -extent.origin.y)];
    extent = CGRectIntegral(image.extent);

    CFRelease(cgImage);
    CFRelease(source);

    if (outImage != NULL) {
        *outImage = image;
    }
    if (outRect != NULL) {
        *outRect = extent;
    }
    return YES;
}

static BOOL ExportMovie(NSURL *inputURL, double durationSeconds, CGFloat startScale, CGFloat endScale, NSURL **outURL, NSError **outError) {
    CIImage *sourceImage = nil;
    CGRect rect = CGRectZero;
    if (!LoadImageAtURL(inputURL, &sourceImage, &rect, outError)) {
        return NO;
    }

    NSInteger width = (NSInteger)CGRectGetWidth(rect);
    NSInteger height = (NSInteger)CGRectGetHeight(rect);
    NSURL *destinationURL = OutputURLForInputURL(inputURL);

    NSFileManager *fileManager = [NSFileManager defaultManager];
    if ([fileManager fileExistsAtPath:destinationURL.path]) {
        [fileManager removeItemAtURL:destinationURL error:nil];
    }

    NSError *writerError = nil;
    AVAssetWriter *writer = [[AVAssetWriter alloc] initWithURL:destinationURL fileType:AVFileTypeQuickTimeMovie error:&writerError];
    if (writer == nil) {
        if (outError != NULL) {
            *outError = writerError;
        }
        return NO;
    }

    AVVideoCodecType codec = SelectCodec(writer, width, height);
    if (codec == nil) {
        if (outError != NULL) {
            *outError = [NSError errorWithDomain:@"PhotoToMov" code:20 userInfo:@{NSLocalizedDescriptionKey: @"No supported H.264 QuickTime codec was available."}];
        }
        return NO;
    }

    NSDictionary *videoSettings = @{
        AVVideoCodecKey: codec,
        AVVideoWidthKey: @(width),
        AVVideoHeightKey: @(height),
        AVVideoCompressionPropertiesKey: @{
            AVVideoAverageBitRateKey: @(kTargetBitrate),
            AVVideoMaxKeyFrameIntervalKey: @(kFramesPerSecond),
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel
        }
    };

    AVAssetWriterInput *input = [[AVAssetWriterInput alloc] initWithMediaType:AVMediaTypeVideo outputSettings:videoSettings];
    input.expectsMediaDataInRealTime = NO;

    NSDictionary *attributes = @{
        (NSString *)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_32BGRA),
        (NSString *)kCVPixelBufferWidthKey: @(width),
        (NSString *)kCVPixelBufferHeightKey: @(height),
        (NSString *)kCVPixelBufferCGImageCompatibilityKey: @YES,
        (NSString *)kCVPixelBufferCGBitmapContextCompatibilityKey: @YES
    };

    if (![writer canAddInput:input]) {
        if (outError != NULL) {
            *outError = [NSError errorWithDomain:@"PhotoToMov" code:21 userInfo:@{NSLocalizedDescriptionKey: @"Unable to add the video input to the writer."}];
        }
        return NO;
    }

    AVAssetWriterInputPixelBufferAdaptor *adaptor =
        [[AVAssetWriterInputPixelBufferAdaptor alloc] initWithAssetWriterInput:input sourcePixelBufferAttributes:attributes];

    [writer addInput:input];

    if (![writer startWriting]) {
        if (outError != NULL) {
            *outError = writer.error ?: [NSError errorWithDomain:@"PhotoToMov" code:22 userInfo:@{NSLocalizedDescriptionKey: @"Unable to start writing the movie."}];
        }
        return NO;
    }

    [writer startSessionAtSourceTime:kCMTimeZero];

    CVPixelBufferPoolRef pool = adaptor.pixelBufferPool;
    if (pool == NULL) {
        if (outError != NULL) {
            *outError = [NSError errorWithDomain:@"PhotoToMov" code:23 userInfo:@{NSLocalizedDescriptionKey: @"The writer did not expose a pixel buffer pool."}];
        }
        return NO;
    }

    CIContext *context = [CIContext contextWithOptions:nil];
    CGColorSpaceRef colorSpace = CGColorSpaceCreateDeviceRGB();
    NSInteger totalFrames = MAX((NSInteger)(durationSeconds * (double)kFramesPerSecond + 0.5), 1);
    CIImage *mask = CenterMask(rect);

    for (NSInteger frameIndex = 0; frameIndex < totalFrames; frameIndex++) {
        while (!input.readyForMoreMediaData) {
            [NSThread sleepForTimeInterval:0.01];
        }

        CVPixelBufferRef buffer = NULL;
        CVReturn status = CVPixelBufferPoolCreatePixelBuffer(NULL, pool, &buffer);
        if (status != kCVReturnSuccess || buffer == NULL) {
            if (colorSpace != NULL) {
                CGColorSpaceRelease(colorSpace);
            }
            if (outError != NULL) {
                *outError = [NSError errorWithDomain:@"PhotoToMov" code:24 userInfo:@{NSLocalizedDescriptionKey: @"Unable to allocate a video frame buffer."}];
            }
            return NO;
        }

        CGFloat denominator = (CGFloat)MAX(totalFrames - 1, 1);
        CGFloat progress = (CGFloat)frameIndex / denominator;
        CGFloat eased = SmoothStep(progress);
        CGFloat scale = startScale + ((endScale - startScale) * eased);

        @autoreleasepool {
            CIImage *frameImage = MakeFrame(sourceImage, rect, mask, scale);
            [context render:frameImage toCVPixelBuffer:buffer bounds:rect colorSpace:colorSpace];
        }

        CMTime presentationTime = CMTimeMake((int32_t)frameIndex, kFramesPerSecond);
        BOOL appended = [adaptor appendPixelBuffer:buffer withPresentationTime:presentationTime];
        CVBufferRelease(buffer);

        if (!appended) {
            if (colorSpace != NULL) {
                CGColorSpaceRelease(colorSpace);
            }
            if (outError != NULL) {
                *outError = writer.error ?: [NSError errorWithDomain:@"PhotoToMov" code:25 userInfo:@{NSLocalizedDescriptionKey: @"Unable to append a rendered frame."}];
            }
            return NO;
        }
    }

    [input markAsFinished];

    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    [writer finishWritingWithCompletionHandler:^{
        dispatch_semaphore_signal(semaphore);
    }];
    dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);

    if (colorSpace != NULL) {
        CGColorSpaceRelease(colorSpace);
    }

    if (writer.status != AVAssetWriterStatusCompleted) {
        if (outError != NULL) {
            *outError = writer.error ?: [NSError errorWithDomain:@"PhotoToMov" code:26 userInfo:@{NSLocalizedDescriptionKey: @"The movie export did not complete."}];
        }
        return NO;
    }

    if (outURL != NULL) {
        *outURL = destinationURL;
    }

    NSLog(@"Created %@", destinationURL.path);
    NSLog(@"Codec: H.264");

    return YES;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *inputPath = nil;
        double durationSeconds = kDefaultDurationSeconds;
        CGFloat startScale = kDefaultStartScale;
        CGFloat endScale = kDefaultEndScale;

        if (argc > 1) {
            inputPath = ExpandPath([NSString stringWithUTF8String:argv[1]]);
        } else {
            printf("Enter the input photo path: ");
            char buffer[4096];
            if (fgets(buffer, sizeof(buffer), stdin) != NULL) {
                inputPath = ExpandPath([NSString stringWithUTF8String:buffer]);
            }
        }

        if (argc > 2) {
            durationSeconds = atof(argv[2]);
        }
        if (argc > 3) {
            startScale = atof(argv[3]);
        }
        if (argc > 4) {
            endScale = atof(argv[4]);
        }

        if (inputPath == nil || inputPath.length == 0) {
            fprintf(stderr, "Error: No input image path was provided.\n");
            return 1;
        }
        if (durationSeconds <= 0.0) {
            fprintf(stderr, "Error: Duration must be greater than zero.\n");
            return 1;
        }
        if (startScale <= 0.0 || endScale <= 0.0) {
            fprintf(stderr, "Error: Zoom values must be greater than zero.\n");
            return 1;
        }

        BOOL isDirectory = NO;
        if (![[NSFileManager defaultManager] fileExistsAtPath:inputPath isDirectory:&isDirectory] || isDirectory) {
            fprintf(stderr, "Error: Unable to find an image at %s\n", inputPath.UTF8String);
            return 1;
        }

        NSURL *inputURL = [NSURL fileURLWithPath:inputPath];
        NSError *error = nil;
        if (!ExportMovie(inputURL, durationSeconds, startScale, endScale, NULL, &error)) {
            fprintf(stderr, "Error: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
    }

    return 0;
}
