CREATE OR REPLACE FUNCTION calculate_interval_yoe(
    p_candidate_id UUID, 
    p_track_only BOOLEAN, 
    p_target_track VARCHAR DEFAULT NULL
)
RETURNS NUMERIC AS $$
DECLARE
    v_total_days INT := 0;
    v_current_start DATE := NULL;
    v_current_end DATE := NULL;
    v_record RECORD;
BEGIN
    FOR v_record IN 
        SELECT start_date, end_date 
        FROM candidate_experiences
        WHERE candidate_id = p_candidate_id
          AND (NOT p_track_only OR matched_track = p_target_track)
        ORDER BY start_date ASC
    LOOP
        IF v_current_start IS NULL THEN
            v_current_start := v_record.start_date;
            v_current_end := v_record.end_date;
        ELSIF v_record.start_date <= v_current_end THEN
            IF v_record.end_date > v_current_end THEN
                v_current_end := v_record.end_date;
            END IF;
        ELSE
            v_total_days := v_total_days + (v_current_end - v_current_start);
            v_current_start := v_record.start_date;
            v_current_end := v_record.end_date;
        END IF;
    END LOOP;

    IF v_current_start IS NOT NULL THEN
        v_total_days := v_total_days + (v_current_end - v_current_start);
    END IF;

    RETURN ROUND((v_total_days::NUMERIC / 365.25), 2);
END;
$$ LANGUAGE plpgsql;