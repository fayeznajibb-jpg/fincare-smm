/**
 * Custom Motion Example
 * 
 * Shows how to create custom Remotion motion effects
 * for static images beyond the built-in options.
 */

import React from 'react';
import {
  AbsoluteFill,
  Img,
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
  staticFile,
} from 'remotion';

// ============================================================================
// Custom Motion Components
// ============================================================================

/**
 * Bouncing ball effect for attention
 */
export const BounceEffect: React.FC<{ src: string; intensity?: number }> = ({
  src,
  intensity = 1,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const bounce = spring({
    frame,
    fps,
    config: {
      damping: 5 * intensity,
      stiffness: 200,
      mass: 1,
    },
  });

  const translateY = interpolate(bounce, [0, 1], [100 * intensity, 0]);

  return (
    <AbsoluteFill style={{ backgroundColor: 'white' }}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          transform: `translateY(${translateY}px)`,
        }}
      />
    </AbsoluteFill>
  );
};

/**
 * Spin and zoom entrance
 */
export const SpinZoomEntrance: React.FC<{ src: string }> = ({ src }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const spinProgress = spring({
    frame,
    fps,
    config: { damping: 10, stiffness: 100 },
  });

  const rotation = interpolate(spinProgress, [0, 1], [360, 0]);
  const scale = interpolate(spinProgress, [0, 1], [0.1, 1]);

  return (
    <AbsoluteFill style={{ backgroundColor: 'white' }}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          transform: `rotate(${rotation}deg) scale(${scale})`,
        }}
      />
    </AbsoluteFill>
  );
};

/**
 * Typewriter text reveal effect
 */
export const TypewriterText: React.FC<{ text: string; speed?: number }> = ({
  text,
  speed = 3,
}) => {
  const frame = useCurrentFrame();
  const charsToShow = Math.floor(frame / speed);
  const displayedText = text.slice(0, charsToShow);

  return (
    <div
      style={{
        position: 'absolute',
        bottom: 100,
        left: '50%',
        transform: 'translateX(-50%)',
        padding: '20px 40px',
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        borderRadius: 8,
      }}
    >
      <span
        style={{
          color: 'white',
          fontSize: 32,
          fontFamily: 'monospace',
        }}
      >
        {displayedText}
        <span
          style={{
            animation: 'blink 1s infinite',
          }}
        >
          |
        </span>
      </span>
      <style>{`
        @keyframes blink {
          0%, 50% { opacity: 1; }
          51%, 100% { opacity: 0; }
        }
      `}</style>
    </div>
  );
};

/**
 * Parallax scrolling background
 */
export const ParallaxBackground: React.FC<{
  src: string;
  speed?: number;
}> = ({ src, speed = 0.5 }) => {
  const frame = useCurrentFrame();
  const translateX = frame * speed;

  return (
    <div
      style={{
        position: 'absolute',
        width: '200%',
        height: '100%',
        transform: `translateX(-${translateX}px)`,
      }}
    >
      <Img
        src={staticFile(src)}
        style={{
          width: '50%',
          height: '100%',
          objectFit: 'cover',
        }}
      />
      <Img
        src={staticFile(src)}
        style={{
          width: '50%',
          height: '100%',
          objectFit: 'cover',
          position: 'absolute',
          left: '50%',
        }}
      />
    </div>
  );
};

/**
 * Ken Burns effect (slow zoom and pan)
 */
export const KenBurnsEffect: React.FC<{
  src: string;
  zoomStart?: number;
  zoomEnd?: number;
  panX?: number;
  panY?: number;
}> = ({ src, zoomStart = 1, zoomEnd = 1.2, panX = 0, panY = 0 }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const scale = interpolate(frame, [0, durationInFrames], [zoomStart, zoomEnd], {
    easing: Easing.linear,
  });

  const translateX = interpolate(
    frame,
    [0, durationInFrames],
    [0, panX],
    { easing: Easing.linear }
  );

  const translateY = interpolate(
    frame,
    [0, durationInFrames],
    [0, panY],
    { easing: Easing.linear }
  );

  return (
    <AbsoluteFill style={{ backgroundColor: 'white', overflow: 'hidden' }}>
      <Img
        src={staticFile(src)}
        style={{
          width: '100%',
          height: '100%',
          objectFit: 'contain',
          transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
        }}
      />
    </AbsoluteFill>
  );
};

/**
 * Glitch effect for dramatic moments
 */
export const GlitchEffect: React.FC<{ src: string }> = ({ src }) => {
  const frame = useCurrentFrame();

  const glitchOffset = Math.sin(frame * 0.5) * 5;
  const rgbShift = Math.sin(frame * 0.3) * 3;

  return (
    <AbsoluteFill style={{ backgroundColor: 'white' }}>
      <div
        style={{
          position: 'relative',
          width: '100%',
          height: '100%',
        }}
      >
        {/* Red channel */}
        <Img
          src={staticFile(src)}
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            transform: `translate(${glitchOffset + rgbShift}px, 0)`,
            mixBlendMode: 'multiply',
            filter: 'url(#red)',
          }}
        />
        {/* Green channel */}
        <Img
          src={staticFile(src)}
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            mixBlendMode: 'multiply',
            filter: 'url(#green)',
          }}
        />
        {/* Blue channel */}
        <Img
          src={staticFile(src)}
          style={{
            position: 'absolute',
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            transform: `translate(${glitchOffset - rgbShift}px, 0)`,
            mixBlendMode: 'multiply',
            filter: 'url(#blue)',
          }}
        />
        <svg style={{ position: 'absolute', width: 0, height: 0 }}>
          <defs>
            <filter id="red">
              <feColorMatrix
                type="matrix"
                values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
              />
            </filter>
            <filter id="green">
              <feColorMatrix
                type="matrix"
                values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
              />
            </filter>
            <filter id="blue">
              <feColorMatrix
                type="matrix"
                values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
              />
            </filter>
          </defs>
        </svg>
      </div>
    </AbsoluteFill>
  );
};

export default BounceEffect;
