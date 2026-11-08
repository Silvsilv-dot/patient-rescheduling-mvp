import React, { useState, useEffect } from 'react';
import StaffDashboard from './components/StaffDashboard';
import ClinicianView from './components/ClinicianView';

const API_BASE_URL = import.meta.env.VITE_BACKEND_URL || '';

export default function App() {
  const [activeTab, setActiveTab] = useState('patient');
  const [userAppointments, setUserAppointments] = useState([]);
  const [toast, setToast] = useState(null);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  // Fetch patient appointments for self-service view
  useEffect(() => {
    async function fetchPatientAppointments() {
      try {
        const res = await fetch(`${API_BASE_URL}/api/appointments/me`);
        if (res.ok) {
          const data = await res.json();
          setUserAppointments(data);
        }
      } catch (err) {
        console.error('Failed to load patient appointments', err);
      }
    }
    if (activeTab === 'patient') {
      fetchPatientAppointments();
    }
  }, [activeTab]);

  // 24-HOUR RESCHEDULING VALIDATION LOCK
  const handleReschedule = (appointmentTime, appointmentId) => {
    const now = new Date();
    const appTime = new Date(appointmentTime);
    const diffInHours = (appTime - now) / (1000 * 60 * 60);

    // Enforce 24-hour rule: block rescheduling if under 24 hours away
    if (diffInHours < 24) {
      showToast("Rescheduling is disabled within 24 hours of the appointment time.", "error");
      return; 
    }

    showToast(`Rescheduling appointment ${appointmentId}...`, "success");
  };

  return (
    <div className="min-h-screen bg-gray-100 text-gray-800">
      {toast && (
        <div className={`fixed top-4 right-4 z-50 p-4 rounded-md text-white font-medium shadow-lg ${toast.type === 'error' ? 'bg-red-600' : 'bg-green-600'}`}>
          {toast.message}
        </div>
      )}

      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
          <span className="font-bold text-xl text-blue-700">Outpatient Scheduler MVP</span>
          <nav className="flex space-x-2">
            <button
              onClick={() => setActiveTab('patient')}
              className={`px-4 py-2 rounded-md text-sm font-medium transition ${
                activeTab === 'patient' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              Patient Portal
            </button>
            <button
              onClick={() => setActiveTab('staff')}
              className={`px-4 py-2 rounded-md text-sm font-medium transition ${
                activeTab === 'staff' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              Front-Desk Staff
            </button>
            <button
              onClick={() => setActiveTab('clinician')}
              className={`px-4 py-2 rounded-md text-sm font-medium transition ${
                activeTab === 'clinician' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              Clinician View
            </button>
          </nav>
        </div>
      </header>

      <main className="py-6">
        {activeTab === 'patient' && (
          <div className="p-6 max-w-4xl mx-auto space-y-6">
            <h1 className="text-2xl font-bold text-gray-900">My Appointments</h1>
            <p className="text-sm text-gray-500">View and manage your upcoming medical consultations.</p>
            
            <div className="space-y-4">
              {userAppointments.length === 0 ? (
                <div className="bg-white p-6 rounded-lg shadow border border-gray-200 text-center text-gray-500">
                  No upcoming appointments found.
                </div>
              ) : (
                userAppointments.map((app) => {
                  const appTime = new Date(app.appointment_time);
                  const isWithin24Hours = (appTime - new Date()) / (1000 * 60 * 60) < 24;

                  return (
                    <div key={app.id} className="bg-white p-5 rounded-lg shadow border border-gray-200 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                      <div>
                        <div className="text-sm font-semibold text-blue-600">{app.clinician_name}</div>
                        <div className="text-lg font-bold text-gray-900">{appTime.toLocaleString()}</div>
                        <div className="text-xs text-gray-500 mt-1">Status: <span className="uppercase font-medium">{app.status}</span></div>
                      </div>
                      <div>
                        <button
                          onClick={() => handleReschedule(app.appointment_time, app.id)}
                          disabled={isWithin24Hours}
                          aria-disabled={isWithin24Hours}
                          data-reschedule-disabled={isWithin24Hours ? "true" : "false"}
                          className={`px-4 py-2 rounded text-sm font-medium transition ${
                            isWithin24Hours 
                              ? 'bg-gray-200 text-gray-400 cursor-not-allowed' 
                              : 'bg-blue-600 text-white hover:bg-blue-700'
                          }`}
                        >
                          {isWithin24Hours ? 'Rescheduling Locked (< 24h)' : 'Reschedule'}
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {activeTab === 'staff' && <StaffDashboard />}
        {activeTab === 'clinician' && <ClinicianView clinicianId="1" />}
      </main>
    </div>
  );
}
