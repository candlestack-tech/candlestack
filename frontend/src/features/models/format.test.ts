import { describe, expect, it } from 'vitest';

import { formatShape, formatSize, formatUsage, splitExtension } from './format';
import { ACCEPTED_EXTENSIONS, frameworkOf, frameworkOfFile } from './frameworks';

const MB = 1024 * 1024;

describe('formatSize', () => {
  it('writes binary megabytes with one decimal', () => {
    expect(formatSize(14_889_779)).toBe('14.2 MB');
    expect(formatSize(3 * MB)).toBe('3.0 MB');
    expect(formatSize(50 * MB)).toBe('50.0 MB');
  });

  it('never writes 0.0 MB for a file that is not empty', () => {
    expect(formatSize(2_000)).toBe('0.1 MB');
    expect(formatSize(0)).toBe('0.0 MB');
  });
});

describe('formatUsage', () => {
  it('drops a trailing zero, as the storage line writes it', () => {
    expect(formatUsage(0, 100 * MB)).toBe('0 MB of 100 MB');
    expect(formatUsage(29.5 * MB, 100 * MB)).toBe('29.5 MB of 100 MB');
    expect(formatUsage(Math.round(14.2 * MB), 100 * MB)).toBe('14.2 MB of 100 MB');
    expect(formatUsage(100 * MB, 100 * MB)).toBe('100 MB of 100 MB');
  });

  it('never reads full while it is not', () => {
    expect(formatUsage(100 * MB - 1, 100 * MB)).toBe('99.9 MB of 100 MB');
    expect(formatUsage(99.97 * MB, 100 * MB)).toBe('99.9 MB of 100 MB');
  });
});

describe('formatShape', () => {
  it('joins the input with × and adds the output after an arrow', () => {
    expect(formatShape([25, 23], [1])).toBe('25×23 → 1');
    expect(formatShape([25, 23], [1], true)).toBe('25 × 23 → 1');
    expect(formatShape([30, 23, 4], [3, 2], true)).toBe('30 × 23 × 4 → 3 × 2');
  });

  it('leaves out an unknown output, and says nothing without an input', () => {
    expect(formatShape([25, 23], null)).toBe('25×23');
    expect(formatShape(null, [1])).toBeNull();
    expect(formatShape([], [1])).toBeNull();
  });
});

describe('splitExtension', () => {
  it('splits the stem from the last extension', () => {
    expect(splitExtension('my-tsmixer.keras')).toEqual(['my-tsmixer', '.keras']);
    expect(splitExtension('v1.2.keras')).toEqual(['v1.2', '.keras']);
    expect(splitExtension('.keras')).toEqual(['.keras', '']);
    expect(splitExtension('model')).toEqual(['model', '']);
  });
});

describe('frameworkOfFile', () => {
  it('knows a framework by the file extension, in any case', () => {
    expect(frameworkOfFile('lstm.keras')).toBe('Keras');
    expect(frameworkOfFile('LSTM.KERAS')).toBe('Keras');
    expect(frameworkOfFile('tcn.pt')).toBe('PyTorch');
    expect(frameworkOfFile('tcn.pth')).toBe('PyTorch');
    expect(frameworkOfFile('mixer.onnx')).toBe('ONNX');
    expect(frameworkOfFile('old.h5')).toBe('TensorFlow');
    expect(frameworkOfFile('saved.pb')).toBe('TensorFlow');
    expect(frameworkOfFile('saved.zip')).toBe('TensorFlow');
    expect(frameworkOfFile('gbm.joblib')).toBe('scikit-learn');
    expect(frameworkOfFile('gbm.pkl')).toBe('scikit-learn');
    expect(frameworkOfFile('gbm.skops')).toBe('scikit-learn');
  });

  it('knows no framework for any other file', () => {
    expect(frameworkOfFile('notes.txt')).toBeNull();
    expect(frameworkOfFile('keras')).toBeNull();
  });

  it('lets the file picker offer the formats the upload names', () => {
    expect(ACCEPTED_EXTENSIONS).toBe('.keras,.onnx');
  });
});

describe('frameworkOf', () => {
  it('knows a framework by the name the API gives it', () => {
    expect(frameworkOf('keras')).toBe('Keras');
    expect(frameworkOf('scikit-learn')).toBe('scikit-learn');
    expect(frameworkOf('jax')).toBeUndefined();
  });
});
